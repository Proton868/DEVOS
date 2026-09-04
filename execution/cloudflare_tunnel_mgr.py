"""Cloudflare Tunnel lifecycle using cloudflared binary when present."""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import tempfile
import time
from pathlib import Path
from typing import Optional

from execution.durable_store import save_tunnel, get_tunnel, new_id
from execution.log_stream import publish_log


class TunnelError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


_PROCS: dict[str, asyncio.subprocess.Process] = {}


def cloudflared_available() -> dict:
    path = shutil.which("cloudflared")
    return {
        "binary": path,
        "available": bool(path),
        "token_configured": bool(os.environ.get("CLOUDFLARE_TUNNEL_TOKEN") or os.environ.get("CLOUDFLARE_TOKEN")),
    }


async def start_tunnel(
    *,
    user_id: str,
    project_id: str,
    local_port: int,
    hostname: Optional[str] = None,
    tunnel_token: Optional[str] = None,
) -> dict:
    info = cloudflared_available()
    token = tunnel_token or os.environ.get("CLOUDFLARE_TUNNEL_TOKEN") or os.environ.get("CLOUDFLARE_TOKEN")
    if not info["available"]:
        raise TunnelError("CLOUDFLARED_MISSING", "cloudflared binary not found on PATH")
    if not token:
        raise TunnelError("CLOUDFLARE_AUTH_REQUIRED", "CLOUDFLARE_TUNNEL_TOKEN not configured")

    tunnel_id = new_id("tun_")
    runtime_log_id = f"tunnel:{tunnel_id}"
    # quick tunnel mode if no hostname: cloudflared tunnel --url http://127.0.0.1:PORT
    # token mode: cloudflared tunnel run --token TOKEN (ingress must point to service)
    cmd: list[str]
    if hostname:
        # Named tunnel with token; write minimal config directing to local port
        cfg_dir = Path(tempfile.mkdtemp(prefix="devos-cf-"))
        cfg = cfg_dir / "config.yml"
        cfg.write_text(
            f"tunnel: {tunnel_id}\n"
            f"credentials-file: {cfg_dir / 'cred.json'}\n"
            f"ingress:\n"
            f"  - hostname: {hostname}\n"
            f"    service: http://127.0.0.1:{local_port}\n"
            f"  - service: http_status:404\n"
        )
        cmd = [info["binary"], "tunnel", "--no-autoupdate", "run", "--token", token]
        config_path = str(cfg)
    else:
        # Ephemeral quick tunnel
        cmd = [
            info["binary"], "tunnel", "--no-autoupdate",
            "--url", f"http://127.0.0.1:{local_port}",
        ]
        config_path = None

    save_tunnel({
        "tunnel_id": tunnel_id, "user_id": user_id, "project_id": project_id,
        "status": "STARTING", "hostname": hostname, "local_port": local_port,
        "config_path": config_path, "created_at": time.time(),
    })
    publish_log(runtime_log_id, "system", f"starting cloudflared: {' '.join(cmd[:4])} ...")

    # Never pass full env with secrets beyond the token in argv (token still sensitive)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except FileNotFoundError as e:
        save_tunnel({"tunnel_id": tunnel_id, "user_id": user_id, "project_id": project_id,
                     "status": "FAILED", "last_error": str(e)})
        raise TunnelError("CLOUDFLARED_MISSING", str(e))

    _PROCS[tunnel_id] = proc
    save_tunnel({
        "tunnel_id": tunnel_id, "user_id": user_id, "project_id": project_id,
        "status": "RUNNING", "hostname": hostname, "local_port": local_port,
        "pid": proc.pid, "started_at": time.time(), "config_path": config_path,
    })

    async def _pump():
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            publish_log(runtime_log_id, "stdout", line)
            # try extract trycloudflare.com URL
            if "https://" in line and "trycloudflare.com" in line:
                for part in line.split():
                    if part.startswith("https://") and "trycloudflare.com" in part:
                        save_tunnel({
                            "tunnel_id": tunnel_id, "user_id": user_id, "project_id": project_id,
                            "status": "READY", "hostname": part, "local_port": local_port,
                            "pid": proc.pid, "started_at": time.time(),
                        })
        code = await proc.wait()
        publish_log(runtime_log_id, "system", f"cloudflared exited {code}")
        save_tunnel({
            "tunnel_id": tunnel_id, "user_id": user_id, "project_id": project_id,
            "status": "STOPPED" if code == 0 else "FAILED",
            "stopped_at": time.time(), "last_error": f"exit {code}",
        })
        _PROCS.pop(tunnel_id, None)

    asyncio.create_task(_pump())
    return {"tunnel_id": tunnel_id, "status": "RUNNING", "local_port": local_port, "pid": proc.pid}


async def stop_tunnel(tunnel_id: str) -> dict:
    proc = _PROCS.get(tunnel_id)
    if proc and proc.returncode is None:
        try:
            proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass
    _PROCS.pop(tunnel_id, None)
    rec = get_tunnel(tunnel_id) or {"tunnel_id": tunnel_id}
    rec.update({"status": "STOPPED", "stopped_at": time.time()})
    save_tunnel(rec)
    return rec


def tunnel_status(tunnel_id: str) -> dict:
    rec = get_tunnel(tunnel_id) or {}
    proc = _PROCS.get(tunnel_id)
    if proc and proc.returncode is None:
        rec["process_alive"] = True
    else:
        rec["process_alive"] = False
        if rec.get("status") in ("RUNNING", "STARTING", "READY") and not rec.get("process_alive"):
            rec["status"] = "STALE"
    return rec
