"""
Artifact foundation — upload, safe archive extraction, hashing, export.
Authority remains FileService / PROJECTS_DIR. No second filesystem.
"""
from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import tarfile
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import BinaryIO, Iterable, Optional

from execution.files import FileService, PathViolation, PROJECTS_DIR

# Configurable limits (sane defaults)
MAX_UPLOAD_SIZE = int(os.environ.get("DEVOS_MAX_UPLOAD_SIZE", str(50 * 1024 * 1024)))
MAX_ARCHIVE_SIZE = int(os.environ.get("DEVOS_MAX_ARCHIVE_SIZE", str(50 * 1024 * 1024)))
MAX_EXTRACTED_SIZE = int(os.environ.get("DEVOS_MAX_EXTRACTED_SIZE", str(200 * 1024 * 1024)))
MAX_ARCHIVE_FILES = int(os.environ.get("DEVOS_MAX_ARCHIVE_FILES", "5000"))
MAX_ARCHIVE_DEPTH = int(os.environ.get("DEVOS_MAX_ARCHIVE_DEPTH", "20"))
MAX_SINGLE_FILE_SIZE = int(os.environ.get("DEVOS_MAX_SINGLE_FILE_SIZE", str(25 * 1024 * 1024)))

_SECRET_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    "id_rsa", "id_ed25519", "credentials.json", "service-account.json",
}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


class ArtifactError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_mime(path: str, sample: bytes = b"") -> str:
    guessed, _ = mimetypes.guess_type(path)
    if guessed:
        return guessed
    if sample.startswith(b"%PDF"):
        return "application/pdf"
    if sample.startswith(b"\x89PNG"):
        return "image/png"
    if sample[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "application/octet-stream"


def is_secret_path(rel: str) -> bool:
    name = rel.replace("\\", "/").split("/")[-1].lower()
    if name in _SECRET_NAMES or name.startswith(".env"):
        return True
    if any(name.endswith(s) for s in _SECRET_SUFFIXES):
        return True
    parts = rel.replace("\\", "/").split("/")
    if any(p == ".git" for p in parts):
        return True
    return False


@dataclass
class ArtifactMeta:
    path: str
    type: str
    mime_type: str
    size: int
    hash: str
    readiness: str = "UNKNOWN"
    verification_state: str = "UNKNOWN"
    previewability: bool = False
    downloadability: bool = True
    shareability: bool = False
    source: str = "workspace"

    def to_dict(self) -> dict:
        return asdict(self)


def artifact_type_for(path: str, mime: str) -> str:
    lower = path.lower()
    if lower.endswith((".html", ".htm")):
        return "html"
    if lower.endswith((".css", ".scss")):
        return "css"
    if lower.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")):
        return "source"
    if lower.endswith((".py", ".go", ".rs", ".java")):
        return "source"
    if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")):
        return "image"
    if lower.endswith((".mp3", ".wav", ".ogg", ".flac")):
        return "audio"
    if lower.endswith((".mp4", ".webm")):
        return "video"
    if lower.endswith((".pdf", ".txt", ".md", ".csv", ".json", ".yaml", ".yml")):
        return "document"
    if lower.endswith((".zip", ".tar", ".gz", ".tgz")):
        return "archive"
    if lower.endswith(".wasm"):
        return "wasm"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    return "other"


def write_bytes(fs: FileService, rel_path: str, data: bytes) -> ArtifactMeta:
    if len(data) > MAX_SINGLE_FILE_SIZE:
        raise ArtifactError("UPLOAD_TOO_LARGE", f"File exceeds {MAX_SINGLE_FILE_SIZE} bytes")
    if is_secret_path(rel_path):
        # Allow write of project files named .env by user intentionally, but flag
        pass
    path = fs._resolve(rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    mime = detect_mime(rel_path, data[:32])
    h = content_hash(data)
    return ArtifactMeta(
        path=rel_path.replace("\\", "/"),
        type=artifact_type_for(rel_path, mime),
        mime_type=mime,
        size=len(data),
        hash=h,
        readiness="PENDING",
        downloadability=not is_secret_path(rel_path),
        previewability=rel_path.lower().endswith(
            (".html", ".htm", ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".md", ".txt")
        ),
    )


def _safe_member_name(name: str) -> str:
    raw = (name or "").replace("\\", "/")
    if not raw or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ArtifactError("ARCHIVE_UNSAFE", f"Absolute path refused: {name!r}")
    if "\x00" in raw:
        raise ArtifactError("ARCHIVE_UNSAFE", "Null byte in path")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ArtifactError("ARCHIVE_UNSAFE", f"Path traversal refused: {name!r}")
    if any(p.startswith(".git") for p in parts):
        raise ArtifactError("ARCHIVE_UNSAFE", "Git internals refused in archive")
    depth = len(parts)
    if depth > MAX_ARCHIVE_DEPTH:
        raise ArtifactError("ARCHIVE_UNSAFE", f"Archive depth exceeds {MAX_ARCHIVE_DEPTH}")
    return "/".join(parts)


def extract_zip(fs: FileService, data: bytes, dest_prefix: str = "") -> list[ArtifactMeta]:
    if len(data) > MAX_ARCHIVE_SIZE:
        raise ArtifactError("ARCHIVE_UNSAFE", f"Archive exceeds {MAX_ARCHIVE_SIZE} bytes")
    out: list[ArtifactMeta] = []
    total = 0
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ArtifactError("ARCHIVE_UNSAFE", f"Malformed zip: {e}") from e
    names = zf.namelist()
    if len(names) > MAX_ARCHIVE_FILES:
        raise ArtifactError("ARCHIVE_UNSAFE", f"Too many archive members (>{MAX_ARCHIVE_FILES})")
    for info in zf.infolist():
        if info.is_dir():
            continue
        # Zip-slip / symlink-ish
        if info.external_attr and ((info.external_attr >> 16) & 0o170000) == 0o120000:
            raise ArtifactError("ARCHIVE_UNSAFE", f"Symlink refused: {info.filename}")
        rel = _safe_member_name(info.filename)
        if dest_prefix:
            rel = f"{dest_prefix.rstrip('/')}/{rel}"
        if info.file_size > MAX_SINGLE_FILE_SIZE:
            raise ArtifactError("ARCHIVE_UNSAFE", f"Member too large: {info.filename}")
        total += info.file_size
        if total > MAX_EXTRACTED_SIZE:
            raise ArtifactError("ARCHIVE_UNSAFE", "Extracted size budget exceeded")
        raw = zf.read(info)
        if len(raw) > MAX_SINGLE_FILE_SIZE:
            raise ArtifactError("ARCHIVE_UNSAFE", f"Member too large after read: {info.filename}")
        meta = write_bytes(fs, rel, raw)
        out.append(meta)
    return out


def extract_tar(fs: FileService, data: bytes, dest_prefix: str = "", mode: str = "r:*") -> list[ArtifactMeta]:
    if len(data) > MAX_ARCHIVE_SIZE:
        raise ArtifactError("ARCHIVE_UNSAFE", f"Archive exceeds {MAX_ARCHIVE_SIZE} bytes")
    out: list[ArtifactMeta] = []
    total = 0
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode=mode)
    except tarfile.TarError as e:
        raise ArtifactError("ARCHIVE_UNSAFE", f"Malformed tar: {e}") from e
    all_members = list(tf.getmembers())
    for m in all_members:
        if m.issym() or m.islnk() or m.isdev() or m.isfifo():
            raise ArtifactError("ARCHIVE_UNSAFE", f"Non-regular member refused: {m.name}")
    members = [m for m in all_members if m.isfile()]
    if len(members) > MAX_ARCHIVE_FILES:
        raise ArtifactError("ARCHIVE_UNSAFE", f"Too many archive members (>{MAX_ARCHIVE_FILES})")
    for m in members:
        rel = _safe_member_name(m.name)
        if dest_prefix:
            rel = f"{dest_prefix.rstrip('/')}/{rel}"
        if m.size > MAX_SINGLE_FILE_SIZE:
            raise ArtifactError("ARCHIVE_UNSAFE", f"Member too large: {m.name}")
        total += m.size
        if total > MAX_EXTRACTED_SIZE:
            raise ArtifactError("ARCHIVE_UNSAFE", "Extracted size budget exceeded")
        f = tf.extractfile(m)
        if f is None:
            continue
        raw = f.read()
        meta = write_bytes(fs, rel, raw)
        out.append(meta)
    return out


def extract_archive(fs: FileService, filename: str, data: bytes, dest_prefix: str = "") -> list[ArtifactMeta]:
    lower = (filename or "").lower()
    if lower.endswith(".zip"):
        return extract_zip(fs, data, dest_prefix)
    if lower.endswith((".tar.gz", ".tgz")):
        return extract_tar(fs, data, dest_prefix, mode="r:gz")
    if lower.endswith(".tar"):
        return extract_tar(fs, data, dest_prefix, mode="r:")
    raise ArtifactError("ARTIFACT_UNSUPPORTED", f"Unsupported archive type: {filename}")


def export_project_zip(
    fs: FileService,
    *,
    exclude_secrets: bool = True,
    exclude_dirs: Optional[Iterable[str]] = None,
) -> bytes:
    """Export workspace as ZIP with safe relative paths. Secrets excluded by default."""
    exclude = set(exclude_dirs or {".git", "node_modules", ".next", "dist", "build", "__pycache__", ".venv"})
    buf = io.BytesIO()
    root = fs.root
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            parts = rel.split("/")
            if any(p in exclude for p in parts):
                continue
            if exclude_secrets and is_secret_path(rel):
                continue
            # re-resolve to ensure still under root
            try:
                fs._resolve(rel)
            except PathViolation:
                continue
            data = path.read_bytes()
            if len(data) > MAX_SINGLE_FILE_SIZE:
                continue
            zf.writestr(rel, data)
    return buf.getvalue()


def list_artifact_metas(fs: FileService, limit: int = 500) -> list[ArtifactMeta]:
    out: list[ArtifactMeta] = []
    root = fs.root
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(p == ".git" for p in rel.split("/")):
            continue
        try:
            data = path.read_bytes()[:65536]
            full_size = path.stat().st_size
            # hash full file if small enough
            if full_size <= MAX_SINGLE_FILE_SIZE:
                h = content_hash(path.read_bytes())
            else:
                h = content_hash(data) + ":partial"
            mime = detect_mime(rel, data[:32])
            out.append(
                ArtifactMeta(
                    path=rel,
                    type=artifact_type_for(rel, mime),
                    mime_type=mime,
                    size=full_size,
                    hash=h,
                    readiness="PENDING",
                    downloadability=not is_secret_path(rel),
                    previewability=rel.lower().endswith(
                        (".html", ".htm", ".css", ".js", ".png", ".jpg", ".svg", ".md", ".txt", ".webp", ".gif")
                    ),
                )
            )
        except OSError:
            continue
        if len(out) >= limit:
            break
    return out


def write_folder_files(fs: "FileService", files: list[tuple[str, bytes]]) -> list[ArtifactMeta]:
    """Write multiple relative paths (browser directory upload). Never trust client paths."""
    out: list[ArtifactMeta] = []
    total = 0
    for rel, data in files:
        # reuse safe member naming rules
        safe = _safe_member_name(rel)
        total += len(data)
        if total > MAX_EXTRACTED_SIZE:
            raise ArtifactError("UPLOAD_TOO_LARGE", "Folder upload exceeds extracted size budget")
        if len(data) > MAX_SINGLE_FILE_SIZE:
            raise ArtifactError("UPLOAD_TOO_LARGE", f"File too large: {safe}")
        out.append(write_bytes(fs, safe, data))
    return out
