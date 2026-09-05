#!/usr/bin/env python3
"""
DevOS CLI — Agency OS Master Plan §8.

Offline-first command-line interface for the Micro profile. Runs the full
DevOS stack without Docker, without Supabase, without any external
dependencies beyond Python and Node.js (for the frontend build).

Usage:
  devos start              Start the server
  devos start --port 3000  Start on a custom port
  devos build              Build the frontend
  devos doctor             Check system health
  devos version            Show version
  devos shell              Interactive REPL
  devos workflow run FILE  Run a workflow from YAML/JSON
  devos research "QUERY"   Run deep research from CLI
  devos audit              Show audit log
  devos billing            Show billing usage
  devos marketplace        List marketplace capabilities
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

VERSION = "4.0.0"
BASE_DIR = Path(__file__).resolve().parent



def _resolve_bind(args):
    """Resolve host/port from CLI args and environment."""
    port = args.port if getattr(args, "port", None) is not None else int(os.getenv("DEVOS_PORT", "8000"))
    host = getattr(args, "host", None) or os.getenv("DEVOS_HOST", "0.0.0.0")
    return host, int(port)


def port_is_in_use(host: str, port: int, timeout: float = 0.4) -> bool:
    """Return True if something is already accepting TCP connections on host:port.

    Prefer connect() over bind(): SO_REUSEADDR and TIME_WAIT make bind checks
    unreliable. When host is 0.0.0.0/:: we probe 127.0.0.1 (and ::1 when useful).
    """
    import socket

    candidates = []
    h = (host or "0.0.0.0").strip().lower()
    if h in ("0.0.0.0", "", "*", "all"):
        candidates = [("127.0.0.1", socket.AF_INET)]
    elif h in ("::", "[::]"):
        candidates = [("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)]
    else:
        family = socket.AF_INET6 if ":" in h else socket.AF_INET
        candidates = [(h.strip("[]"), family)]

    for addr, family in candidates:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                if sock.connect_ex((addr, int(port))) == 0:
                    return True
        except OSError:
            continue
    return False


def _print_already_running(host: str, port: int) -> None:
    print(f"\n  DevOS is already running on http://{host}:{port}")
    print("  Use: systemctl status devos")
    print("  To restart: sudo systemctl restart devos")
    print(f"  Or start on a free port: ./devos start --port {int(port) + 1}")
    print()



def cmd_start(args):
    """Start the DevOS server (foreground). Production is owned by systemd."""
    host, port = _resolve_bind(args)

    # Fail closed before importing the full app / binding Uvicorn.
    if port_is_in_use(host, port):
        _print_already_running(host, port)
        # Non-error exit: the service is healthy; the operator asked to start
        # what is already running under systemd (or another owner).
        sys.exit(0)

    import uvicorn
    from core.config import settings

    print(f"\n  ⚡ DevOS v{VERSION} — Micro Profile")
    print(f"  🌐 http://{host}:{port}")
    print(f"  📋 http://{host}:{port}/api/health\n")
    # uvicorn doesn't support reload + workers together
    workers = settings.WEB_CONCURRENCY if not args.dev else 1
    if args.dev and settings.WEB_CONCURRENCY > 1:
        print(f"  ⚠️  Reload mode enabled — forcing workers=1 (uvicorn limitation)")
    try:
        uvicorn.run(
            "app:app",
            host=host,
            port=port,
            reload=args.dev,
            workers=workers,
            log_level="info" if not args.quiet else "warning",
        )
    except OSError as exc:
        err = str(exc).lower()
        if getattr(exc, "errno", None) in (98, 48) or "address already in use" in err:
            _print_already_running(host, port)
            sys.exit(1)
        raise



def cmd_build(args):
    """Build the frontend (developer convenience — not required after ./install.sh)."""
    import shutil
    import re as _re

    frontend_dir = BASE_DIR / "frontend-src"
    if not frontend_dir.exists():
        print("ERROR: frontend-src/ not found. Run from the DevOS root directory.")
        sys.exit(1)

    try:
        node_ver = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("ERROR: Node.js not found. Frontend build requires Node.js 22 LTS.")
        print("  Run ./install.sh (provisions Node automatically), or install Node 22 manually.")
        sys.exit(1)

    major = node_ver.lstrip("v").split(".")[0]
    if major != "22":
        print(f"WARNING: Node {node_ver} detected; this project standardizes on Node 22 LTS.")
        print("  Continuing, but prefer Node 22 for reproducible builds.")

    try:
        subprocess.run(["npm", "--version"], capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("ERROR: npm not found. Install Node.js 22 LTS (includes npm).")
        sys.exit(1)

    pkg = frontend_dir / "package.json"
    lock = frontend_dir / "package-lock.json"
    if not pkg.exists():
        print("ERROR: frontend-src/package.json missing.")
        sys.exit(1)
    if not lock.exists():
        print("ERROR: frontend-src/package-lock.json missing — cannot run npm ci.")
        sys.exit(1)

    node_modules = frontend_dir / "node_modules"
    if not node_modules.exists():
        print("node_modules missing — running npm ci...")
        env = os.environ.copy()
        env.setdefault("npm_config_registry", "https://registry.npmjs.org/")
        result = subprocess.run(
            ["npm", "ci", "--no-audit", "--no-fund"],
            cwd=str(frontend_dir),
            env=env,
            capture_output=not args.verbose,
            text=True,
        )
        if result.returncode != 0:
            print("ERROR: npm ci failed:")
            if result.stderr:
                print(result.stderr)
            sys.exit(1)
        print("Frontend dependencies installed")

    print("Building frontend...")
    env = os.environ.copy()
    env["CI"] = "true"
    env.setdefault("npm_config_registry", "https://registry.npmjs.org/")
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(frontend_dir),
        env=env,
        capture_output=not args.verbose,
        text=True,
    )
    if result.returncode != 0:
        print("ERROR: Build failed:")
        if result.stderr:
            print(result.stderr)
        if result.stdout and not args.verbose:
            print(result.stdout[-2000:])
        sys.exit(1)

    build_dir = frontend_dir / "build"
    frontend_out = BASE_DIR / "frontend"
    if not (build_dir / "index.html").exists() or not (build_dir / "static").exists():
        print("ERROR: Build output not found at frontend-src/build/")
        sys.exit(1)

    static_dir = frontend_out / "static"
    templates_dir = frontend_out / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    if static_dir.exists():
        shutil.rmtree(str(static_dir), ignore_errors=True)
    shutil.copytree(str(build_dir / "static"), str(static_dir))
    shutil.copy2(str(build_dir / "index.html"), str(templates_dir / "index.html"))
    shutil.copy2(str(build_dir / "index.html"), str(frontend_out / "index.html"))
    manifest = build_dir / "asset-manifest.json"
    if manifest.exists():
        shutil.copy2(str(manifest), str(frontend_out / "asset-manifest.json"))

    html = (templates_dir / "index.html").read_text()
    refs = _re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
    missing = [r for r in refs if not (frontend_out / r.lstrip("/")).exists()]
    if missing:
        print("ERROR: HTML references missing static assets:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)

    print("Frontend built and synced to frontend/")


def cmd_doctor(args):
    """Check runtime health (Node/npm optional after install)."""
    print(f"DevOS v{VERSION} — System Check")
    print()
    print("DevOS Runtime")
    runtime_ok = True

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 11)
    mark = "OK" if py_ok else "FAIL"
    extra = "" if py_ok else " (3.11+ required)"
    print(f"  [{mark}] Python: {py_ver}{extra}")
    runtime_ok = runtime_ok and py_ok

    venv_py = BASE_DIR / ".venv" / "bin" / "python"
    venv_ok = venv_py.exists()
    mark = "OK" if venv_ok else "FAIL"
    print(f"  [{mark}] Virtualenv: {'.venv' if venv_ok else 'missing (run ./install.sh)'}")
    runtime_ok = runtime_ok and venv_ok

    env_file = BASE_DIR / ".env"
    env_ok = env_file.exists()
    mark = "OK" if env_ok else "FAIL"
    print(f"  [{mark}] Environment: {'.env' if env_ok else 'missing'}")
    runtime_ok = runtime_ok and env_ok

    data_dir = BASE_DIR / "data"
    data_ok = data_dir.is_dir()
    mark = "OK" if data_ok else "FAIL"
    print(f"  [{mark}] Data: {'data/' if data_ok else 'missing'}")
    runtime_ok = runtime_ok and data_ok

    workspace = BASE_DIR / "workspace"
    ws_ok = workspace.is_dir()
    mark = "OK" if ws_ok else "FAIL"
    print(f"  [{mark}] Workspace: {'workspace/' if ws_ok else 'missing'}")
    runtime_ok = runtime_ok and ws_ok

    index_html = BASE_DIR / "frontend" / "templates" / "index.html"
    static_dir = BASE_DIR / "frontend" / "static"
    fe_ok = index_html.exists() and static_dir.is_dir()
    mark = "OK" if fe_ok else "FAIL"
    detail = "templates/index.html + static/" if fe_ok else "missing runtime assets"
    print(f"  [{mark}] Frontend: {detail}")
    runtime_ok = runtime_ok and fe_ok

    if fe_ok:
        import re as _re
        html = index_html.read_text()
        refs = _re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
        missing = [r for r in refs if not (BASE_DIR / "frontend" / r.lstrip("/")).exists()]
        assets_ok = not missing
        mark = "OK" if assets_ok else "FAIL"
        msg = "all referenced files present" if assets_ok else f"missing {len(missing)} file(s)"
        print(f"  [{mark}] Runtime assets: {msg}")
        runtime_ok = runtime_ok and assets_ok

    app_py = BASE_DIR / "app.py"
    cli_py = BASE_DIR / "cli.py"
    files_ok = app_py.exists() and cli_py.exists()
    mark = "OK" if files_ok else "FAIL"
    print(f"  [{mark}] Application files: {'app.py + cli.py' if files_ok else 'incomplete'}")
    runtime_ok = runtime_ok and files_ok

    print()
    print("Frontend Build Tooling (not required for ./devos start after install)")
    try:
        node_ver = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip()
        print(f"  [info] Node.js: {node_ver} (available; not required for runtime)")
    except FileNotFoundError:
        print("  [info] Node.js: not found (not required for runtime after ./install.sh)")

    try:
        npm_ver = subprocess.run(["npm", "--version"], capture_output=True, text=True).stdout.strip()
        print(f"  [info] npm: {npm_ver} (available; not required for runtime)")
    except FileNotFoundError:
        print("  [info] npm: not found (not required for runtime after ./install.sh)")

    print()
    if runtime_ok:
        print("Runtime health OK — you can run: ./devos start")
    else:
        print("Runtime checks failed. Run ./install.sh to complete installation.")
        print("  (./devos build is only for developers modifying the frontend.)")


def cmd_version(args):
    """Show version."""
    print(f"DevOS v{VERSION}")
    print(f"Python {sys.version}")
    print(f"Profile: {os.getenv('DEVOS_PROFILE', 'micro')}")


def cmd_shell(args):
    """Interactive REPL."""
    print(f"DevOS v{VERSION} Shell — Type 'help' for commands, 'exit' to quit\n")
    while True:
        try:
            cmd = input("devos> ").strip()
            if not cmd:
                continue
            if cmd == "exit" or cmd == "quit":
                break
            elif cmd == "help":
                print("Commands: help, exit, version, status, audit, billing, marketplace")
            elif cmd == "version":
                print(f"DevOS v{VERSION}")
            elif cmd == "status":
                try:
                    import httpx
                    r = httpx.get("http://localhost:8000/api/health")
                    print(f"Server: {'✅ online' if r.status_code == 200 else '❌ offline'}")
                except Exception:
                    print("Server: ❌ offline (not running)")
            elif cmd == "audit":
                from governance.audit import get_audit_logger
                entries = get_audit_logger().query(limit=10)
                for e in entries:
                    print(f"  [{e['timestamp'][:19]}] {e['event_type']} — {e['action']} → {e['outcome']}")
            elif cmd == "billing":
                from governance.billing import get_billing
                usage = get_billing().get_usage("default")
                print(f"  LLM tokens: {usage.llm_tokens} (${usage.llm_cost:.6f})")
                print(f"  Execution: {usage.execution_seconds:.1f}s (${usage.execution_cost:.6f})")
                print(f"  API calls: {usage.api_calls} (${usage.api_cost:.6f})")
                print(f"  Total: ${usage.total_cost:.6f}")
            elif cmd == "marketplace":
                from governance.marketplace import get_marketplace
                entries = get_marketplace().list(limit=10)
                for e in entries:
                    print(f"  {e.icon} {e.name} ({e.slug}) — {e.pricing.value}")
            else:
                print(f"Unknown command: {cmd}")
        except (KeyboardInterrupt, EOFError):
            print()
            break
        except Exception as e:
            print(f"Error: {e}")


def cmd_workflow(args):
    """Run a workflow from YAML or JSON file."""
    path = Path(args.file)
    if not path.exists():
        print(f"❌ File not found: {args.file}")
        sys.exit(1)

    content = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        from brain.workflow import Workflow
        workflow = Workflow.from_yaml(content)
    elif path.suffix == ".json":
        import json
        from brain.workflow import Workflow
        workflow = Workflow.from_dict(json.loads(content))
    else:
        print(f"❌ Unsupported format: {path.suffix}")
        sys.exit(1)

    valid, errors = workflow.validate()
    if not valid:
        print("❌ Invalid workflow:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print(f"✅ Workflow '{workflow.name}' is valid")
    print(f"   Steps: {len(workflow.steps)}")
    print(f"   Triggers: {', '.join(workflow.triggers)}")
    print(f"\nUCIP ExecutionPlan:")
    print(json.dumps(workflow.to_ucip_plan(), indent=2))


def cmd_research(args):
    """Run deep research from CLI."""
    import asyncio

    async def _research():
        from brain.research import DeepResearchAgent
        agent = DeepResearchAgent()
        print(f"🔍 Researching: {args.query}")
        report = await agent.research(args.query, max_sources=args.sources)
        print(f"\n{'='*60}")
        print(f"📋 {report.question}")
        print(f"{'='*60}")
        print(f"\n{report.summary}\n")
        for section in report.sections:
            print(f"## {section.get('heading', '')}")
            print(f"{section.get('content', '')}\n")
        print(f"Confidence: {report.confidence:.0%}")
        print(f"Sources: {len(report.sources)} | Citations: {len(report.citations)}")
        if report.gaps:
            print(f"Gaps: {', '.join(report.gaps)}")

    asyncio.run(_research())


def main():
    parser = argparse.ArgumentParser(
        description=f"DevOS v{VERSION} — Agency Operating System",
        prog="devos",
    )
    sub = parser.add_subparsers(dest="command")

    # start
    p_start = sub.add_parser("start", help="Start the server")
    p_start.add_argument("--port", type=int, help="Port to listen on")
    p_start.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    p_start.add_argument("--dev", action="store_true", help="Enable auto-reload")
    p_start.add_argument("--quiet", action="store_true", help="Suppress logs")

    # build
    p_build = sub.add_parser("build", help="Build the frontend")
    p_build.add_argument("--verbose", action="store_true", help="Show build output")

    # doctor
    sub.add_parser("doctor", help="Check system health")

    # version
    sub.add_parser("version", help="Show version")

    # shell
    sub.add_parser("shell", help="Interactive REPL")

    # workflow
    p_wf = sub.add_parser("workflow", help="Workflow operations")
    p_wf.add_argument("action", choices=["run", "validate"], help="Action")
    p_wf.add_argument("file", help="YAML or JSON workflow file")

    # research
    p_research = sub.add_parser("research", help="Deep research")
    p_research.add_argument("query", help="Research question")
    p_research.add_argument("--sources", type=int, default=5, help="Max sources")

    # audit
    sub.add_parser("audit", help="Show audit log")

    # billing
    sub.add_parser("billing", help="Show billing usage")

    # marketplace
    sub.add_parser("marketplace", help="List marketplace capabilities")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "start": cmd_start,
        "build": cmd_build,
        "doctor": cmd_doctor,
        "version": cmd_version,
        "shell": cmd_shell,
        "workflow": cmd_workflow,
        "research": cmd_research,
        "audit": cmd_audit,
        "billing": cmd_billing,
        "marketplace": cmd_marketplace,
    }

    commands[args.command](args)


def cmd_audit(args):
    """Show audit log."""
    from governance.audit import get_audit_logger
    entries = get_audit_logger().query(limit=10)
    if not entries:
        print("No audit entries found.")
        return
    for e in entries:
        print(f"  [{e['timestamp'][:19]}] {e['event_type']} — {e['action']} → {e['outcome']}")


def cmd_billing(args):
    """Show billing usage."""
    from governance.billing import get_billing
    usage = get_billing().get_usage("default")
    print(f"  LLM tokens: {usage.llm_tokens} (${usage.llm_cost:.6f})")
    print(f"  Execution: {usage.execution_seconds:.1f}s (${usage.execution_cost:.6f})")
    print(f"  API calls: {usage.api_calls} (${usage.api_cost:.6f})")
    print(f"  Total: ${usage.total_cost:.6f}")


def cmd_marketplace(args):
    """List marketplace capabilities."""
    from governance.marketplace import get_marketplace
    entries = get_marketplace().list(limit=10)
    if not entries:
        print("No marketplace entries found.")
        return
    for e in entries:
        print(f"  {e.icon} {e.name} ({e.slug}) — {e.pricing.value}")


if __name__ == "__main__":
    main()