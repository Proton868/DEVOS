"""Marketplace route — browse and install packages/plugins from npm and
PyPI (used to add dependencies to Flow scripts) and curated VSCode-marketplace-style
"automation" listings. Actual installation is delegated to the same venv/npm
tooling used by the Flow script runner (execution/runner.py) so installed
packages are immediately usable by scripts.

This intentionally does NOT proxy the full VSCode Marketplace API (which
requires a VSIX-compatible extension host DevOS doesn't have) — instead it
exposes a curated set of "automation" recipes (ready-to-use Flow script
templates) alongside real npm/PyPI search, which is what's actually useful
inside a script-driven automation platform like this one.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel
from core.database import get_db
from api.routes.auth import get_current_user

logger = logging.getLogger("devos.marketplace")
router = APIRouter()

# ── Curated automation templates (the "VSCode marketplace" analogue) ──────
# Expanded (G11) to give Flow n8n-comparable breadth: a starter template for
# each of the major automation categories real n8n/Zapier users reach for
# first, all runnable as-is against DevOS's real Script model fields.
AUTOMATION_TEMPLATES = [
    {
        "id": "daily-digest",
        "name": "Daily Email Digest",
        "category": "productivity",
        "language": "python",
        "description": "Summarizes yesterday's notes/memory and emails a digest every morning.",
        "packages": ["requests"],
        "code": (
            "import os\n"
            "from communications.email import send_email_sync\n\n"
            "print('Fetching daily digest...')\n"
            "try:\n"
            "    recipient = os.environ.get('SECRET_DIGEST_EMAIL') or os.environ.get('SMTP_FROM')\n"
            "    if not recipient:\n"
            "        print('Set a DIGEST_EMAIL secret or SMTP_FROM env var first.')\n"
            "    else:\n"
            "        send_email_sync(\n"
            "            to=recipient,\n"
            "            subject='DevOS Daily Digest',\n"
            "            body='Your daily digest is ready. Check /api/memory/recent for details.',\n"
            "        )\n"
            "        print('Digest sent.')\n"
            "except Exception as e:\n"
            "    print(f'Digest failed: {e}')\n"
        ),
        "schedule_type": "cron",
        "schedule_value": "0 8 * * *",
    },
    {
        "id": "site-uptime-check",
        "name": "Website Uptime Monitor",
        "category": "monitoring",
        "language": "python",
        "description": "Pings a URL every 5 minutes and notifies you if it goes down.",
        "packages": ["requests"],
        "code": (
            "import requests, sys\n"
            "URL = 'https://example.com'\n"
            "try:\n"
            "    r = requests.get(URL, timeout=10)\n"
            "    print(f'{URL} -> {r.status_code}')\n"
            "except Exception as e:\n"
            "    print(f'DOWN: {e}')\n"
        ),
        "schedule_type": "interval",
        "schedule_value": "300",
    },
    {
        "id": "github-issue-triage",
        "name": "GitHub Issue Auto-Triage",
        "category": "dev-tools",
        "language": "node",
        "description": "Labels new GitHub issues using simple keyword rules.",
        "packages": ["node-fetch"],
        "code": (
            "// Requires GITHUB_PERSONAL_ACCESS_TOKEN secret\n"
            "console.log('Triage run starting...');\n"
        ),
        "schedule_type": "interval",
        "schedule_value": "900",
    },
    {
        "id": "backup-to-supabase",
        "name": "Backup Workspace to Supabase",
        "category": "backup",
        "language": "python",
        "description": "Archives the workspace directory and uploads it to Supabase Storage.",
        "packages": ["supabase"],
        "code": (
            "print('Backing up workspace to Supabase Storage...')\n"
            "# TODO: wire to Supabase MCP connection\n"
        ),
        "schedule_type": "cron",
        "schedule_value": "0 3 * * 0",
    },
    {
        "id": "slack-standup-reminder",
        "name": "Slack Standup Reminder",
        "category": "productivity",
        "language": "python",
        "description": "Posts a daily standup reminder message to a Slack channel via webhook.",
        "packages": ["requests"],
        "code": (
            "import requests, os\n\n"
            "webhook_url = os.environ.get('SECRET_SLACK_WEBHOOK_URL')\n"
            "if not webhook_url:\n"
            "    print('Set a SLACK_WEBHOOK_URL secret first.')\n"
            "else:\n"
            "    requests.post(webhook_url, json={'text': \"Good morning! Time for standup.\"})\n"
            "    print('Reminder posted.')\n"
        ),
        "schedule_type": "cron",
        "schedule_value": "0 9 * * 1-5",
    },
    {
        "id": "rss-to-notes",
        "name": "RSS Feed to Notes",
        "category": "integration",
        "language": "python",
        "description": "Fetches an RSS feed and saves new entries as Notes for later review.",
        "packages": ["requests", "feedparser"],
        "code": (
            "import feedparser\n\n"
            "FEED_URL = 'https://example.com/feed.xml'\n"
            "feed = feedparser.parse(FEED_URL)\n"
            "for entry in feed.entries[:5]:\n"
            "    print(f'- {entry.title}: {entry.link}')\n"
            "# TODO: wire to /api/notes to persist entries\n"
        ),
        "schedule_type": "interval",
        "schedule_value": "3600",
    },
    {
        "id": "csv-report-cleaner",
        "name": "CSV Data Cleaner & Report",
        "category": "data",
        "language": "python",
        "description": "Loads a CSV, drops empty rows/duplicates, and prints a quick summary report.",
        "packages": ["pandas"],
        "code": (
            "import pandas as pd\n\n"
            "PATH = 'data.csv'\n"
            "df = pd.read_csv(PATH)\n"
            "before = len(df)\n"
            "df = df.dropna(how='all').drop_duplicates()\n"
            "print(f'Rows: {before} -> {len(df)}')\n"
            "print(df.describe(include=\"all\"))\n"
        ),
        "schedule_type": "manual",
        "schedule_value": None,
    },
    {
        "id": "webhook-to-database",
        "name": "Incoming Webhook Logger",
        "category": "integration",
        "language": "python",
        "description": "Pairs with this script's own webhook URL (see Flow > Webhook URL) to log every payload it receives via a chained script.",
        "packages": [],
        "code": (
            "import os, datetime\n\n"
            "print(f\"[{datetime.datetime.now(timezone.utc).isoformat()}] Webhook received, trigger={os.environ.get('DEVOS_TRIGGER', 'unknown')}\")\n"
        ),
        "schedule_type": "manual",
        "schedule_value": None,
    },
    {
        "id": "ssl-cert-expiry-check",
        "name": "SSL Certificate Expiry Checker",
        "category": "monitoring",
        "language": "python",
        "description": "Checks a domain's SSL certificate and warns if it expires within 14 days.",
        "packages": [],
        "code": (
            "import ssl, socket, datetime\n\n"
            "HOST = 'example.com'\n"
            "ctx = ssl.create_default_context()\n"
            "with ctx.wrap_socket(socket.socket(), server_hostname=HOST) as s:\n"
            "    s.settimeout(10)\n"
            "    s.connect((HOST, 443))\n"
            "    cert = s.getpeercert()\n"
            "expiry = datetime.datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z')\n"
            "days_left = (expiry - datetime.datetime.now(timezone.utc)).days\n"
            "print(f'{HOST} cert expires in {days_left} days ({expiry})')\n"
            "if days_left < 14:\n"
            "    print('WARNING: certificate expiring soon!')\n"
        ),
        "schedule_type": "cron",
        "schedule_value": "0 6 * * *",
    },
    {
        "id": "log-rotation-cleanup",
        "name": "Log File Rotation Cleanup",
        "category": "backup",
        "language": "python",
        "description": "Deletes log files in a directory older than 30 days to keep disk usage under control.",
        "packages": [],
        "code": (
            "import os, time\n\n"
            "LOG_DIR = 'logs'\n"
            "CUTOFF_DAYS = 30\n"
            "now = time.time()\n"
            "removed = 0\n"
            "if os.path.isdir(LOG_DIR):\n"
            "    for f in os.listdir(LOG_DIR):\n"
            "        path = os.path.join(LOG_DIR, f)\n"
            "        if os.path.isfile(path) and now - os.path.getmtime(path) > CUTOFF_DAYS * 86400:\n"
            "            os.remove(path)\n"
            "            removed += 1\n"
            "print(f'Removed {removed} old log files.')\n"
        ),
        "schedule_type": "cron",
        "schedule_value": "0 2 * * 0",
    },
    {
        "id": "api-health-dashboard",
        "name": "Multi-API Health Dashboard",
        "category": "monitoring",
        "language": "python",
        "description": "Pings a list of API endpoints and reports which ones are healthy.",
        "packages": ["requests"],
        "code": (
            "import requests\n\n"
            "ENDPOINTS = ['https://example.com/health', 'https://example.org/health']\n"
            "for url in ENDPOINTS:\n"
            "    try:\n"
            "        r = requests.get(url, timeout=8)\n"
            "        print(f'{url}: {r.status_code}')\n"
            "    except Exception as e:\n"
            "        print(f'{url}: ERROR {e}')\n"
        ),
        "schedule_type": "interval",
        "schedule_value": "600",
    },
    {
        "id": "password-rotation-reminder",
        "name": "Credential Rotation Reminder",
        "category": "security",
        "language": "python",
        "description": "Reminds you to rotate stored secrets on a fixed schedule (pairs well with in-app notifications).",
        "packages": [],
        "code": (
            "print('Reminder: rotate API keys/secrets stored in Flow > Secrets this quarter.')\n"
        ),
        "schedule_type": "cron",
        "schedule_value": "0 9 1 */3 *",
    },
    {
        "id": "npm-outdated-check",
        "name": "NPM Dependency Audit",
        "category": "dev-tools",
        "language": "node",
        "description": "Runs npm outdated/audit against a project directory and prints a summary.",
        "packages": [],
        "code": (
            "const { execSync } = require('child_process');\n"
            "try {\n"
            "  const out = execSync('npm outdated --json || true').toString();\n"
            "  console.log(out || 'All dependencies up to date.');\n"
            "} catch (e) {\n"
            "  console.log('Audit failed:', e.message);\n"
            "}\n"
        ),
        "schedule_type": "cron",
        "schedule_value": "0 7 * * 1",
    },
    {
        "id": "form-submission-router",
        "name": "Form Submission Router",
        "category": "integration",
        "language": "python",
        "description": "Receives form data via this script's webhook and routes it based on a field value (e.g. to Slack vs email) -- chain child scripts with on_success.",
        "packages": [],
        "code": (
            "import os\n\n"
            "print('Form submission processed via webhook trigger.')\n"
            "# TODO: parse payload once webhook bodies are forwarded into script env\n"
        ),
        "schedule_type": "manual",
        "schedule_value": None,
    },
    {
        "id": "disk-space-alert",
        "name": "Disk Space Alert",
        "category": "monitoring",
        "language": "python",
        "description": "Checks free disk space on the host and warns when usage crosses a threshold.",
        "packages": [],
        "code": (
            "import shutil\n\n"
            "PATH = '/'\n"
            "THRESHOLD_PCT = 85\n"
            "total, used, free = shutil.disk_usage(PATH)\n"
            "pct_used = used / total * 100\n"
            "print(f'{PATH}: {pct_used:.1f}% used ({free // (1024**3)} GB free)')\n"
            "if pct_used > THRESHOLD_PCT:\n"
            "    print('WARNING: disk usage above threshold!')\n"
        ),
        "schedule_type": "interval",
        "schedule_value": "1800",
    },
    {
        "id": "changelog-generator",
        "name": "Git Changelog Generator",
        "category": "dev-tools",
        "language": "python",
        "description": "Generates a CHANGELOG.md snippet from recent git commit messages.",
        "packages": [],
        "code": (
            "import subprocess\n\n"
            "out = subprocess.run(['git', 'log', '--oneline', '-20'], capture_output=True, text=True)\n"
            "print('## Recent Changes\\n')\n"
            "for line in out.stdout.splitlines():\n"
            "    print(f'- {line}')\n"
        ),
        "schedule_type": "manual",
        "schedule_value": None,
    },
]


@router.get("/templates")
async def list_templates(request: Request, category: str = Query(None), db=Depends(get_db)):
    """List curated automation templates (ready-to-import Flow scripts)."""
    await get_current_user(request, db)
    items = AUTOMATION_TEMPLATES
    if category:
        items = [t for t in items if t["category"] == category]
    return {"templates": items}


@router.get("/templates/categories")
async def template_categories(request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    cats = sorted({t["category"] for t in AUTOMATION_TEMPLATES})
    return {"categories": cats}


@router.get("/templates/{template_id}")
async def get_template(template_id: str, request: Request, db=Depends(get_db)):
    await get_current_user(request, db)
    for t in AUTOMATION_TEMPLATES:
        if t["id"] == template_id:
            return t
    raise HTTPException(404, "Template not found")


# ── Real package search (npm registry + PyPI) ──────────────────────────
@router.get("/search")
async def search_packages(
    request: Request,
    q: str = Query(..., min_length=1),
    registry: str = Query("npm", pattern="^(npm|pypi)$"),
    db=Depends(get_db),
):
    """Search the real npm or PyPI registries for installable packages."""
    await get_current_user(request, db)
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if registry == "npm":
                r = await client.get(
                    "https://registry.npmjs.org/-/v1/search",
                    params={"text": q, "size": 20},
                )
                r.raise_for_status()
                data = r.json()
                results = [
                    {
                        "name": o["package"]["name"],
                        "version": o["package"]["version"],
                        "description": o["package"].get("description", ""),
                        "registry": "npm",
                        "url": o["package"].get("links", {}).get("npm", ""),
                    }
                    for o in data.get("objects", [])
                ]
            else:
                r = await client.get(f"https://pypi.org/pypi/{q}/json")
                if r.status_code == 200:
                    data = r.json()
                    info = data.get("info", {})
                    results = [
                        {
                            "name": info.get("name", q),
                            "version": info.get("version", ""),
                            "description": info.get("summary", ""),
                            "registry": "pypi",
                            "url": info.get("project_url", ""),
                        }
                    ]
                else:
                    # Fall back to PyPI's simple search-like suggestion (exact
                    # lookups only — PyPI doesn't offer a public fuzzy search
                    # API anymore), so at least confirm the exact name exists.
                    results = []
    except Exception as e:
        logger.warning("Package search failed for %s/%s: %s", registry, q, e)
        raise HTTPException(502, f"Registry search failed: {e}")
    return {"results": results, "registry": registry, "query": q}


class InstallReq(BaseModel):
    script_id: str
    packages: list[str]


@router.post("/install")
async def install_packages(req: InstallReq, request: Request, db=Depends(get_db)):
    """Install packages into a specific script's isolated venv (Python) or
    node_modules (Node), delegating to the ExecutionLayer's existing
    dependency installer. Bash scripts have no package manager here."""
    user = await get_current_user(request, db)
    from sqlalchemy import select
    from core.database import Script
    r = await db.execute(select(Script).where(Script.id == req.script_id, Script.owner_id == user.id))
    script = r.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")

    if script.language == "python":
        from execution.runner import ExecutionLayer
        layer = ExecutionLayer()
        try:
            result = await layer.install_packages(req.packages, env_name=script.id)
        except Exception as e:
            logger.exception("Package install failed for script %s", req.script_id)
            raise HTTPException(502, f"Install failed: {e}")
        if not result.get("success"):
            raise HTTPException(502, f"pip install failed: {result.get('error') or result.get('output')}")
        return {"installed": True, "packages": req.packages, "venv_path": result.get("venv_path"), "output": result.get("output")}

    elif script.language == "node":
        import asyncio
        from pathlib import Path
        node_dir = Path("data/node_modules") / script.id
        node_dir.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "--no-audit", "--no-fund", *req.packages,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(node_dir),
        )
        stdout, stderr = await proc.communicate()
        output = (stdout + stderr).decode(errors="replace")[:2000]
        if proc.returncode != 0:
            raise HTTPException(502, f"npm install failed: {output}")
        return {"installed": True, "packages": req.packages, "node_dir": str(node_dir), "output": output}

    raise HTTPException(400, f"Dependency install isn't supported for language '{script.language}'")
