
"""Governed Flutter/Dart toolchain detection and SDK provisioning."""
from __future__ import annotations
import hashlib, json, logging, os, re, shutil, stat, tarfile, tempfile, threading, zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger('devos.flutter_toolchain')
DEFAULT_SDK_ROOT = Path(os.environ.get('DEVOS_FLUTTER_SDK_ROOT') or (Path('data') / 'toolchains' / 'flutter')).resolve()
_ALLOWED_HOSTS = frozenset({'storage.googleapis.com'})
_ALLOWED_PATH_PREFIX = '/flutter_infra_release/releases/'
DEFAULT_FLUTTER_VERSION = os.environ.get('DEVOS_FLUTTER_VERSION', '3.24.5')
CAP_FLUTTER_SDK_PROVISION = 'ucip:toolchain.flutter_sdk_provision'
CHECKSUMS_PATH = Path(os.environ.get('DEVOS_FLUTTER_CHECKSUMS') or (Path('data') / 'toolchains' / 'flutter_checksums.json'))
_INSTALL_LOCKS: dict = {}
_INSTALL_LOCKS_GUARD = threading.Lock()

def _lock_for(key: str):
    with _INSTALL_LOCKS_GUARD:
        if key not in _INSTALL_LOCKS:
            _INSTALL_LOCKS[key] = threading.Lock()
        return _INSTALL_LOCKS[key]

@dataclass
class FlutterDetectResult:
    is_flutter_project: bool
    has_pubspec: bool
    flutter_sdk_dep: bool
    dart_markers: list = field(default_factory=list)
    structure_markers: list = field(default_factory=list)
    confidence: float = 0.0
    reasons: list = field(default_factory=list)
    def to_dict(self): return asdict(self)

@dataclass
class FlutterRuntimeInfo:
    flutter_available: bool
    dart_available: bool
    flutter_path: Optional[str] = None
    dart_path: Optional[str] = None
    flutter_version: Optional[str] = None
    dart_version: Optional[str] = None
    channel: Optional[str] = None
    managed_sdk: bool = False
    managed_sdk_root: Optional[str] = None
    error: Optional[str] = None
    platform_limits: list = field(default_factory=list)
    def to_dict(self): return asdict(self)
    @property
    def ok(self): return bool(self.flutter_available)

def _read_fs_paths(fs):
    try:
        tree = fs.tree(max_depth=5) if hasattr(fs, 'tree') else []
        return {(i.get('path') or '').replace('\\', '/').lstrip('./') for i in (tree or []) if i.get('type') in ('file','dir')}
    except Exception:
        return set()

def _read_text(fs, path):
    try:
        if hasattr(fs, 'read'):
            r = fs.read(path)
            return r.get('content') if isinstance(r, dict) else (str(r) if r is not None else None)
    except Exception:
        return None
    return None

def detect_flutter_project(fs):
    paths = _read_fs_paths(fs)
    lower = {p.lower() for p in paths}
    has_pubspec = any(p == 'pubspec.yaml' or p.endswith('/pubspec.yaml') for p in lower)
    reasons, structure, dart_markers = [], [], []
    for marker in ('lib/', 'lib/main.dart', 'android/', 'ios/', 'macos/', 'web/', 'linux/', 'windows/'):
        if any(p == marker.rstrip('/') or p.startswith(marker) or p.endswith('/'+marker.rstrip('/')) for p in lower):
            structure.append(marker.rstrip('/'))
    if any(p.endswith('.dart') for p in lower): dart_markers.append('*.dart')
    flutter_sdk_dep = False
    body = _read_text(fs, 'pubspec.yaml') if has_pubspec else None
    if body and ('sdk: flutter' in body or 'sdk:flutter' in body.replace(' ','') or re.search(r'(?m)^\s*flutter\s*:\s*$', body)):
        flutter_sdk_dep = True
        reasons.append('pubspec declares flutter sdk')
    conf = 0.0
    if has_pubspec: conf += 0.35; reasons.append('pubspec.yaml present')
    if flutter_sdk_dep: conf += 0.4
    if 'lib' in structure or 'lib/main.dart' in structure: conf += 0.15
    if any(p in structure for p in ('android','ios','macos','web')): conf += 0.1
    is_fl = bool(has_pubspec and (flutter_sdk_dep or conf >= 0.5))
    if has_pubspec and not flutter_sdk_dep and not structure:
        is_fl = False
    return FlutterDetectResult(is_fl, has_pubspec, flutter_sdk_dep, dart_markers, structure, min(1.0, conf), reasons)

def _parse_flutter_version(output):
    m = re.search(r'Flutter\s+(\d+\.\d+\.\d+[^\s]*)', output or '')
    ver = m.group(1) if m else None
    m2 = re.search(r'channel\s+(\w+)', output or '', re.I)
    return ver, (m2.group(1) if m2 else None)

def _parse_dart_version(output):
    m = re.search(r'Dart\s+SDK\s+version:\s*(\S+)', output or '', re.I)
    if m: return m.group(1)
    m = re.search(r'version:\s*(\d+\.\d+\.\d+[^\s]*)', output or '', re.I)
    return m.group(1) if m else None

def platform_build_limits():
    import sys
    limits = []
    if sys.platform != 'darwin':
        limits.append('flutter build ios requires macOS + Xcode; unavailable on this host')
        limits.append('flutter build macos requires macOS')
    if sys.platform.startswith('linux'):
        limits.append('ios/macos builds require a macOS host')
    return limits

def probe_flutter_runtime(*, which_fn=None, run_fn=None, prefer_managed=True, sdk_root=None):
    import shutil as _sh
    which = which_fn or _sh.which
    root = Path(sdk_root or DEFAULT_SDK_ROOT)
    managed = None
    if prefer_managed and root.is_dir():
        cand = root / 'flutter' / 'bin' / 'flutter'
        if cand.is_file() and os.access(cand, os.X_OK):
            managed = str(cand.resolve())
    flutter_path = managed or which('flutter')
    dart_path = None
    if managed:
        dc = root / 'flutter' / 'bin' / 'dart'
        if dc.is_file(): dart_path = str(dc.resolve())
    if not dart_path: dart_path = which('dart')
    info = FlutterRuntimeInfo(bool(flutter_path), bool(dart_path), flutter_path, dart_path, managed_sdk=bool(managed), managed_sdk_root=str(root) if managed else None, platform_limits=platform_build_limits())
    if not flutter_path:
        info.error = 'toolchain_unavailable'; return info
    def _run(argv):
        if run_fn: return run_fn(argv)
        import subprocess
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            return p.returncode, p.stdout or '', p.stderr or ''
        except Exception as e:
            return 1, '', f'{type(e).__name__}: {e}'
    code, out, err = _run([flutter_path, '--version'])
    comb = (out or '') + '\n' + (err or '')
    if code == 0 or 'Flutter' in comb:
        ver, ch = _parse_flutter_version(comb)
        info.flutter_version, info.channel = ver, ch
        info.dart_version = _parse_dart_version(comb)
    if dart_path and not info.dart_version:
        code, out, err = _run([dart_path, '--version'])
        info.dart_version = _parse_dart_version((out or '') + (err or '')) or info.dart_version
    return info

def flutter_command_plan(*, upgrade=False, build_target='apk'):
    target = re.sub(r'[^a-z0-9_\-]+', '', (build_target or 'apk').lower()) or 'apk'
    cmds = {'install': 'flutter pub get', 'analyze': 'flutter analyze', 'test': 'flutter test', 'build': f'flutter build {target} --debug', 'format': 'dart format .', 'doctor': 'flutter doctor -v'}
    if upgrade: cmds['upgrade_deps'] = 'flutter pub upgrade'
    return {'toolchain': 'flutter', 'package_manager': 'flutter', 'commands': cmds, 'requires_install': True, 'platform_limits': platform_build_limits(), 'notes': ['pub upgrade only when explicitly requested', 'SDK provisioning is a separate authorized capability', 'ios builds require macOS host']}

@dataclass
class ProvisionRequest:
    version: str = DEFAULT_FLUTTER_VERSION
    channel: str = 'stable'
    platform: str = 'linux'
    authorized: bool = False
    authorization_token: Optional[str] = None
    download_fn: Optional[Callable] = None
    checksum_fn: Optional[Callable] = None
    sdk_root: Optional[Path] = None
    require_checksum: bool = False

@dataclass
class ProvisionResult:
    ok: bool
    error: Optional[str] = None
    error_code: Optional[str] = None
    version: Optional[str] = None
    flutter_path: Optional[str] = None
    install_root: Optional[str] = None
    evidence: dict = field(default_factory=dict)
    def to_dict(self): return asdict(self)

def _release_archive_url(version, channel, platform):
    ver = re.sub(r'[^0-9a-zA-Z.\-]+', '', version)
    ch = re.sub(r'[^a-z]+', '', channel.lower()) or 'stable'
    plat = {'linux':'linux','macos':'macos','windows':'windows'}.get(platform.lower(), 'linux')
    return f'https://storage.googleapis.com/flutter_infra_release/releases/{ch}/{plat}/flutter_{plat}_{ver}-{ch}.tar.xz'

def validate_download_url(url):
    try: parsed = urlparse(url)
    except Exception: return False, 'invalid_url'
    if parsed.scheme != 'https': return False, 'url_must_be_https'
    host = (parsed.hostname or '').lower()
    if host not in _ALLOWED_HOSTS: return False, f'host_not_allowed:{host}'
    path = parsed.path or ''
    if not path.startswith(_ALLOWED_PATH_PREFIX): return False, 'path_not_allowed'
    if '..' in path or path.startswith('//'): return False, 'path_traversal'
    return True, 'ok'

def load_pinned_checksums():
    if not CHECKSUMS_PATH.is_file(): return {}
    try:
        data = json.loads(CHECKSUMS_PATH.read_text())
        if not isinstance(data, dict): return {}
        out = {}
        for k, v in data.items():
            if str(k).startswith('_'): continue
            sv = str(v).strip().lower()
            if not sv or sv.startswith('replace') or len(sv) < 32: continue
            out[str(k)] = str(v)
        return out
    except Exception as e:
        logger.warning('checksums file unreadable: %s', e)
        return {}

def resolve_expected_checksum(url, checksum_fn=None):
    if checksum_fn: return checksum_fn(url)
    pins = load_pinned_checksums()
    if url in pins: return pins[url]
    base = url.rsplit('/', 1)[-1]
    for k, v in pins.items():
        if k.endswith(base) or k == base: return v
    return None

def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def safe_extract_tar(archive, dest):
    dest = dest.resolve(); dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode='r:*') as tf:
        for member in tf.getmembers():
            name = member.name
            if not name or name.startswith('/') or name.startswith('\\'): raise ValueError(f'archive_absolute_path:{name}')
            if any(p == '..' for p in Path(name).parts): raise ValueError(f'archive_path_traversal:{name}')
            target = (dest / name).resolve()
            try: target.relative_to(dest)
            except ValueError as e: raise ValueError(f'archive_path_escape:{name}') from e
            if member.issym() or member.islnk():
                link = member.linkname or ''
                if link.startswith('/') or '..' in Path(link).parts: raise ValueError(f'archive_symlink_escape:{name}->{link}')
                link_target = (target.parent / link).resolve() if not link.startswith('/') else Path(link)
                try: link_target.relative_to(dest)
                except ValueError as e: raise ValueError(f'archive_symlink_escape:{name}->{link}') from e
            if member.isfile() or member.isdir() or member.issym():
                tf.extract(member, path=dest, set_attrs=False)

def safe_extract_zip(archive, dest):
    dest = dest.resolve(); dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'r') as zf:
        for info in zf.infolist():
            name = info.filename
            if not name or name.startswith('/'): raise ValueError(f'archive_absolute_path:{name}')
            if any(p == '..' for p in Path(name).parts): raise ValueError(f'archive_path_traversal:{name}')
            target = (dest / name).resolve()
            try: target.relative_to(dest)
            except ValueError as e: raise ValueError(f'archive_path_escape:{name}') from e
            if info.is_dir(): target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, 'r') as src, open(target, 'wb') as out:
                    shutil.copyfileobj(src, out)

def _default_download(url, dest):
    ok, reason = validate_download_url(url)
    if not ok: raise ValueError(f'download_url_rejected:{reason}')
    import urllib.request
    req = urllib.request.Request(url, method='GET', headers={'User-Agent': 'DevOS-FlutterProvision/1.0'})
    with urllib.request.urlopen(req, timeout=120) as resp:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, 'wb') as f: shutil.copyfileobj(resp, f)


def parse_pubspec_constraints(pubspec_text: str | None) -> dict:
    """Extract Dart/Flutter environment constraints from pubspec.yaml text.

    Does not install anything. Returns required sdk / flutter constraints
    for compatibility checks (never silently substitutes incompatible versions).
    """
    out = {"dart_sdk": None, "flutter": None, "raw_environment": {}}
    if not pubspec_text:
        return out
    # Simple YAML-ish parse without full PyYAML dependency
    env_block = False
    for line in pubspec_text.splitlines():
        s = line.strip()
        if s.startswith("environment:"):
            env_block = True
            continue
        if env_block:
            if not line.startswith(" ") and not line.startswith("\t") and s and not s.startswith("#"):
                env_block = False
            else:
                m = re.match(r"\s*(sdk|flutter)\s*:\s*[\'\"]?([^\'#]+)[\'\"]?", line)
                if m:
                    key, val = m.group(1), m.group(2).strip().strip("'\"")
                    out["raw_environment"][key] = val
                    if key == "sdk":
                        out["dart_sdk"] = val
                    elif key == "flutter":
                        out["flutter"] = val
    return out


def _version_tuple(v: str | None):
    if not v:
        return None
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", v.strip())
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def constraint_compatible(installed: str | None, constraint: str | None) -> bool | None:
    """Best-effort semver constraint check. None if cannot determine safely."""
    if not constraint or not installed:
        return None
    c = constraint.strip()
    inst = _version_tuple(installed)
    if inst is None:
        return None
    # >=x.y.z <a.b.c or ^x.y.z or single version
    if c.startswith("^"):
        base = _version_tuple(c[1:])
        if not base:
            return None
        return inst >= base and inst[0] == base[0]
    if c.startswith(">="):
        parts = re.split(r"\s+", c)
        lower = _version_tuple(parts[0][2:])
        upper = None
        for p in parts[1:]:
            if p.startswith("<"):
                upper = _version_tuple(p[1:].lstrip("="))
        if lower and inst < lower:
            return False
        if upper and inst >= upper:
            return False
        return True if lower else None
    single = _version_tuple(c)
    if single:
        return inst == single
    return None


def flutter_project_status(fs, *, runtime_info=None) -> dict:
    """Structured Flutter status for API/agents (no secrets)."""
    from execution.toolchain import structured_status, TOOLCHAIN_UNAVAILABLE, VERSION_INCOMPATIBLE
    det = detect_flutter_project(fs)
    body = _read_text(fs, "pubspec.yaml") if det.has_pubspec else None
    constraints = parse_pubspec_constraints(body)
    info = runtime_info or probe_flutter_runtime()
    compatible = None
    if constraints.get("dart_sdk") and info.dart_version:
        compatible = constraint_compatible(info.dart_version, constraints["dart_sdk"])
    if constraints.get("flutter") and info.flutter_version:
        fc = constraint_compatible(info.flutter_version, constraints["flutter"])
        if compatible is None:
            compatible = fc
        elif fc is False:
            compatible = False
    if not det.is_flutter_project:
        status = "unavailable"
        err = None
    elif not info.flutter_available:
        status = "unavailable"
        err = TOOLCHAIN_UNAVAILABLE
    elif compatible is False:
        status = "version_incompatible"
        err = VERSION_INCOMPATIBLE
    else:
        status = "ready"
        err = None
    return structured_status(
        kind="flutter",
        detected=det.is_flutter_project,
        available=bool(info.flutter_available),
        required_version=constraints.get("flutter") or constraints.get("dart_sdk"),
        installed_version=info.flutter_version,
        compatible=compatible,
        status=status,
        error=err,
        platform_limits=info.platform_limits,
        extra={
            "detection": det.to_dict(),
            "constraints": constraints,
            "runtime": info.to_dict(),
            "provision_capability": CAP_FLUTTER_SDK_PROVISION,
            "commands": flutter_command_plan()["commands"] if info.flutter_available else {},
        },
    )

def provision_flutter_sdk(req: ProvisionRequest) -> ProvisionResult:
    evidence = {'capability': CAP_FLUTTER_SDK_PROVISION, 'authorized': bool(req.authorized), 'version_requested': req.version, 'channel': req.channel, 'platform': req.platform}
    if not req.authorized:
        return ProvisionResult(False, 'SDK provisioning requires explicit authorization (HITL / UCIP capability grant)', 'authorization_required', evidence=evidence)
    version = re.sub(r'[^0-9a-zA-Z.\-]+', '', req.version or '') or DEFAULT_FLUTTER_VERSION
    channel = re.sub(r'[^a-z]+', '', (req.channel or 'stable').lower()) or 'stable'
    platform = (req.platform or 'linux').lower()
    if platform not in ('linux','macos','windows'):
        return ProvisionResult(False, 'unsupported_platform', 'unsupported_platform', evidence=evidence)
    root = Path(req.sdk_root or DEFAULT_SDK_ROOT).resolve()
    root_s = str(root)
    for p in ('/usr','/bin','/sbin','/opt','/System'):
        if root_s == p or root_s.startswith(p + os.sep):
            return ProvisionResult(False, 'install_root_forbidden', 'install_root_forbidden', evidence={**evidence, 'root': root_s})
    lock = _lock_for(f'{root}:{version}:{channel}')
    if not lock.acquire(blocking=False):
        return ProvisionResult(False, 'install_in_progress', 'concurrent_install', evidence=evidence)
    staging = None
    try:
        flutter_bin = root / 'flutter' / 'bin' / 'flutter'
        if flutter_bin.is_file() and os.access(flutter_bin, os.X_OK):
            info = probe_flutter_runtime(which_fn=lambda _: None, prefer_managed=True, sdk_root=root)
            if info.flutter_available:
                evidence['idempotent'] = True
                evidence['flutter_version'] = info.flutter_version
                return ProvisionResult(True, version=info.flutter_version or version, flutter_path=str(flutter_bin), install_root=str(root), evidence=evidence)
        root.mkdir(parents=True, exist_ok=True)
        parent = root.parent if root.parent.is_dir() else Path(tempfile.gettempdir())
        staging = Path(tempfile.mkdtemp(prefix='flutter-sdk-stage-', dir=str(parent)))
        archive_path = staging / 'flutter_sdk_archive'
        url = _release_archive_url(version, channel, platform)
        ok_url, url_reason = validate_download_url(url)
        evidence['url'] = url
        evidence['url_validation'] = url_reason
        if not ok_url:
            return ProvisionResult(False, f'url_rejected:{url_reason}', 'url_rejected', evidence=evidence)
        download = req.download_fn or _default_download
        try: download(url, archive_path)
        except Exception as e:
            evidence['download_error'] = str(e)[:300]
            return ProvisionResult(False, str(e)[:300], 'download_failed', evidence=evidence)
        if not archive_path.is_file() or archive_path.stat().st_size < 64:
            return ProvisionResult(False, 'archive_empty', 'archive_invalid', evidence=evidence)
        digest = _sha256_file(archive_path)
        evidence['sha256'] = digest
        expected = resolve_expected_checksum(url, req.checksum_fn)
        if expected is not None:
            evidence['sha256_expected'] = expected
            if digest.lower() != str(expected).lower():
                return ProvisionResult(False, 'checksum_mismatch', 'checksum_failure', evidence=evidence)
        elif req.require_checksum:
            return ProvisionResult(False, 'checksum_required_but_missing', 'checksum_required', evidence=evidence)
        extract_dir = staging / 'extract'; extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            if archive_path.read_bytes()[:2] == b'PK': safe_extract_zip(archive_path, extract_dir)
            else: safe_extract_tar(archive_path, extract_dir)
        except ValueError as e:
            msg = str(e); code = 'archive_unsafe'
            if 'traversal' in msg: code = 'archive_traversal'
            elif 'symlink' in msg: code = 'symlink_escape'
            return ProvisionResult(False, msg, code, evidence=evidence)
        except Exception as e:
            return ProvisionResult(False, f'extract_failed:{e}', 'extract_failed', evidence=evidence)
        found = None
        for cand in extract_dir.rglob('flutter'):
            if cand.is_file() and cand.parent.name == 'bin': found = cand; break
        if not found:
            alt = extract_dir / 'flutter' / 'bin' / 'flutter'
            if alt.is_file(): found = alt
        if not found:
            return ProvisionResult(False, 'flutter_binary_missing_in_archive', 'invalid_sdk_layout', evidence=evidence)
        sdk_flutter_dir = root / 'flutter'
        src_sdk = found.parent.parent
        if sdk_flutter_dir.exists(): shutil.rmtree(sdk_flutter_dir, ignore_errors=True)
        shutil.move(str(src_sdk), str(sdk_flutter_dir))
        bin_dir = sdk_flutter_dir / 'bin'
        if bin_dir.is_dir():
            for p in bin_dir.iterdir():
                if p.is_file(): p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        flutter_bin = bin_dir / 'flutter'
        if not flutter_bin.is_file():
            shutil.rmtree(sdk_flutter_dir, ignore_errors=True)
            return ProvisionResult(False, 'post_install_binary_missing', 'partial_install_cleaned', evidence=evidence)
        (root / 'INSTALLED.json').write_text(json.dumps({'version': version, 'channel': channel, 'platform': platform, 'sha256': digest, 'url': url}, indent=2))
        evidence['installed'] = True
        evidence['flutter_path'] = str(flutter_bin)
        return ProvisionResult(True, version=version, flutter_path=str(flutter_bin.resolve()), install_root=str(root), evidence=evidence)
    except Exception as e:
        logger.exception('flutter sdk provision failed')
        try:
            partial = root / 'flutter'
            if partial.exists() and not (partial / 'bin' / 'flutter').is_file():
                shutil.rmtree(partial, ignore_errors=True)
        except Exception: pass
        return ProvisionResult(False, str(e)[:300], 'provision_failed', evidence=evidence)
    finally:
        lock.release()
        if staging and staging.exists(): shutil.rmtree(staging, ignore_errors=True)

def assert_no_system_package_manager(cmd):
    low = (cmd or '').lower()
    forbidden = ('sudo ', 'apt-get', 'apt install', 'yum install', 'dnf install', 'pacman -', 'brew install', 'curl |', 'wget |', '| bash', '| sh', 'chmod +x /usr', 'mv /usr')
    return not any(f in low for f in forbidden)

async def request_sdk_provision_hitl(*, version=DEFAULT_FLUTTER_VERSION, channel='stable', platform='linux', actor_id='agent', user_id='user', loop_id='toolchain', reason='Flutter SDK not available; governed provisioning requested'):
    from governance.hitl import HITLQueue
    import json as _json
    queue = HITLQueue()
    action_input = _json.dumps({'version': version, 'channel': channel, 'platform': platform, 'capability': CAP_FLUTTER_SDK_PROVISION, 'install_root': str(DEFAULT_SDK_ROOT)})
    description = f'Provision Flutter SDK {version} ({channel}/{platform}) into {DEFAULT_SDK_ROOT}. No sudo/apt; official archive only.'
    req = await queue.submit(loop_id=loop_id, agent_id=actor_id, user_id=user_id, action='provision_flutter_sdk', action_input=action_input, description=description, cap_required=CAP_FLUTTER_SDK_PROVISION, reason=reason)
    approved = await queue.wait_for_decision(req.id)
    if not approved:
        return {'ok': False, 'error_code': 'hitl_denied', 'error': 'Human denied or timed out Flutter SDK provisioning', 'hitl_id': req.id, 'capability': CAP_FLUTTER_SDK_PROVISION}
    result = provision_flutter_sdk(ProvisionRequest(version=version, channel=channel, platform=platform, authorized=True))
    out = result.to_dict(); out['hitl_id'] = req.id; out['hitl_approved'] = True
    return out
