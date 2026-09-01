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


def cmd_start(args):
    """Start the DevOS server."""
    import uvicorn
    from core.config import settings
    port = args.port or int(os.getenv("DEVOS_PORT", "8000"))
    host = args.host or os.getenv("DEVOS_HOST", "0.0.0.0")
    print(f"\n  ⚡ DevOS v{VERSION} — Micro Profile")
    print(f"  🌐 http://{host}:{port}")
    print(f"  📋 http://{host}:{port}/api/health\n")
    # uvicorn doesn't support reload + workers together
    workers = settings.WEB_CONCURRENCY if not args.dev else 1
    if args.dev and settings.WEB_CONCURRENCY > 1:
        print(f"  ⚠️  Reload mode enabled — forcing workers=1 (uvicorn limitation)")
    uvicorn.run("app:app", host=host, port=port, reload=args.dev,
                workers=workers,
                log_level="info" if not args.quiet else "warning")


def cmd_build(args):
    """Build the frontend (React)."""
    frontend_dir = BASE_DIR / "frontend-src"
    if not frontend_dir.exists():
        print("❌ frontend-src/ not found. Run from the DevOS root directory.")
        sys.exit(1)

    print("📦 Building frontend...")
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(frontend_dir),
        capture_output=not args.verbose,
        text=True,
    )
    if result.returncode != 0:
        print("❌ Build failed:")
        print(result.stderr)
        sys.exit(1)

    # Sync build output to frontend/
    import shutil
    build_dir = frontend_dir / "build"
    frontend_out = BASE_DIR / "frontend"
    if build_dir.exists():
        static_dir = frontend_out / "static"
        templates_dir = frontend_out / "templates"
        static_dir.mkdir(parents=True, exist_ok=True)
        templates_dir.mkdir(parents=True, exist_ok=True)

        # Clean and copy
        if (build_dir / "static").exists():
            shutil.rmtree(str(static_dir), ignore_errors=True)
            shutil.copytree(str(build_dir / "static"), str(static_dir))
        if (build_dir / "index.html").exists():
            shutil.copy2(str(build_dir / "index.html"), str(templates_dir / "index.html"))

        print("✅ Frontend built and synced to frontend/")
    else:
        print("❌ Build output not found at frontend-src/build/")
        sys.exit(1)


def cmd_doctor(args):
    """Check system health."""
    print(f"DevOS v{VERSION} — System Check\n")

    checks = []

    # Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks.append(("Python", py_ver, sys.version_info >= (3, 10)))

    # Node.js
    try:
        node_ver = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip()
        checks.append(("Node.js", node_ver, True))
    except FileNotFoundError:
        checks.append(("Node.js", "not found", False))

    # npm
    try:
        npm_ver = subprocess.run(["npm", "--version"], capture_output=True, text=True).stdout.strip()
        checks.append(("npm", npm_ver, True))
    except FileNotFoundError:
        checks.append(("npm", "not found", False))

    # Git
    try:
        git_ver = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
        checks.append(("Git", git_ver, True))
    except FileNotFoundError:
        checks.append(("Git", "not found", False))

    # .env
    env_file = BASE_DIR / ".env"
    checks.append((".env file", "exists" if env_file.exists() else "missing", env_file.exists()))

    # Data directory
    data_dir = BASE_DIR / "data"
    checks.append(("data/ directory", "exists" if data_dir.exists() else "missing", data_dir.exists()))

    # Frontend build
    index_html = BASE_DIR / "frontend" / "templates" / "index.html"
    checks.append(("Frontend build", "exists" if index_html.exists() else "missing", index_html.exists()))

    for name, status, ok in checks:
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}: {status}")

    all_ok = all(c[2] for c in checks)
    print(f"\n{'✅ All checks passed!' if all_ok else '❌ Some checks failed. Run devos build to fix frontend.'}")


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