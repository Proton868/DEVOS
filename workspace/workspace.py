"""
DevOS Workspace — AIS-OS concepts adapted for DevOS.
From nateherkai/AIS-OS: /onboard, /audit, /level-up, 3Ms framework, connections registry.
Stored in data/workspace/ — persists across sessions.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("devos.workspace")

WORKSPACE_DIR = Path("data/workspace")
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


class Workspace:
    """
    Personal AI Operating System workspace.
    Stores user context, connections, decisions, and skills.
    One workspace per user — think of it as the user's CLAUDE.md equivalent.
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.path = WORKSPACE_DIR / user_id
        self.path.mkdir(parents=True, exist_ok=True)

    # ── Files ─────────────────────────────────────────────────────────────────

    @property
    def _context_file(self) -> Path:
        return self.path / "context.json"

    @property
    def _connections_file(self) -> Path:
        return self.path / "connections.json"

    @property
    def _decisions_file(self) -> Path:
        return self.path / "decisions.jsonl"

    @property
    def _skills_file(self) -> Path:
        return self.path / "skills.json"

    @property
    def _audit_file(self) -> Path:
        return self.path / "audit.json"

    # ── Context (from /onboard) ───────────────────────────────────────────────

    def get_context(self) -> dict:
        if not self._context_file.exists():
            return {}
        return json.loads(self._context_file.read_text())

    def save_context(self, context: dict):
        context["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._context_file.write_text(json.dumps(context, indent=2))

    def build_context_block(self) -> str:
        """Build a system prompt context block from the workspace."""
        ctx = self.get_context()
        if not ctx:
            return ""
        lines = ["[User Workspace Context]"]
        if ctx.get("identity"):
            lines.append(f"Identity: {ctx['identity']}")
        if ctx.get("business"):
            lines.append(f"Business: {ctx['business']}")
        if ctx.get("priorities"):
            lines.append(f"Current priorities: {ctx['priorities']}")
        if ctx.get("voice_style"):
            lines.append(f"Communication style: {ctx['voice_style']}")
        conns = self.get_connections()
        if conns:
            lines.append(f"Connected systems: {', '.join(c['name'] for c in conns if c.get('status') == 'connected')}")
        return "\n".join(lines)

    # ── /onboard skill ────────────────────────────────────────────────────────

    async def run_onboard(self, answers: dict) -> dict:
        """
        Process onboarding answers (7 questions) and save workspace context.
        AIS-OS: fills CLAUDE.md from aios-intake.md answers.
        """
        context = {
            "identity": answers.get("q1", ""),      # Who are you + what you do
            "business": answers.get("q2", ""),       # Business/project description
            "icp": answers.get("q3", ""),            # Ideal customer/user
            "voice_sample": answers.get("q4", ""),   # Writing voice sample
            "priorities": answers.get("q5", ""),     # Quarterly priorities
            "connected_tools": answers.get("q6", ""), # Tools/systems in use
            "open_questions": answers.get("q7", ""),  # What to figure out
            "onboarded_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save_context(context)

        # Auto-populate connections from tool mentions
        tool_text = answers.get("q6", "")
        known_tools = ["stripe", "supabase", "notion", "airtable", "zapier",
                       "github", "slack", "discord", "shopify", "vercel",
                       "railway", "render", "fly.io", "google sheets",
                       "quickbooks", "hubspot", "mailchimp", "twilio"]
        connections = self.get_connections()
        existing_names = {c["name"].lower() for c in connections}
        for tool in known_tools:
            if tool.lower() in tool_text.lower() and tool.lower() not in existing_names:
                self.add_connection(tool.title(), "mentioned in onboarding", "not_connected")

        return {"status": "onboarded", "context": context}

    # ── Connections registry ──────────────────────────────────────────────────

    def get_connections(self) -> list[dict]:
        if not self._connections_file.exists():
            return []
        return json.loads(self._connections_file.read_text())

    def add_connection(self, name: str, description: str,
                       status: str = "not_connected",
                       mechanism: str = "",
                       api_key_env: str = "") -> dict:
        connections = self.get_connections()
        conn = {
            "name": name, "description": description,
            "status": status, "mechanism": mechanism,
            "api_key_env": api_key_env,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        connections.append(conn)
        self._connections_file.write_text(json.dumps(connections, indent=2))
        return conn

    def update_connection(self, name: str, **updates) -> bool:
        connections = self.get_connections()
        for c in connections:
            if c["name"].lower() == name.lower():
                c.update(updates)
                self._connections_file.write_text(json.dumps(connections, indent=2))
                return True
        return False

    # ── Decision log (append-only) ────────────────────────────────────────────

    def log_decision(self, decision: str, reasoning: str,
                     alternatives: Optional[list] = None) -> dict:
        """Append-only decision log — AIS-OS decisions/log.md equivalent."""
        entry = {
            "id": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            "decision": decision,
            "reasoning": reasoning,
            "alternatives": alternatives or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(self._decisions_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def get_decisions(self, limit: int = 20) -> list[dict]:
        if not self._decisions_file.exists():
            return []
        lines = self._decisions_file.read_text().strip().split("\n")
        decisions = []
        for line in lines:
            if line.strip():
                try:
                    decisions.append(json.loads(line))
                except Exception:
                    pass
        return decisions[-limit:]

    # ── Skills registry ───────────────────────────────────────────────────────

    def get_skills(self) -> list[dict]:
        if not self._skills_file.exists():
            return self._default_skills()
        return json.loads(self._skills_file.read_text())

    def _default_skills(self) -> list[dict]:
        return [
            {"name": "onboard", "description": "7-question workspace setup interview", "last_run": None},
            {"name": "audit", "description": "Four-Cs gap report: Context, Connections, Capabilities, Cadence", "last_run": None},
            {"name": "level-up", "description": "3Ms interview: find one automation to ship this week", "last_run": None},
            {"name": "sprint", "description": "Break a goal into a 1-week sprint with daily tasks", "last_run": None},
            {"name": "review", "description": "Review code or plan for issues before shipping", "last_run": None},
        ]

    # ── /audit skill ──────────────────────────────────────────────────────────

    async def run_audit(self, provider: Optional[str] = None) -> dict:
        """
        Four-Cs gap report. AIS-OS /audit skill.
        Context, Connections, Capabilities, Cadence.
        """
        from brain.llm import BrainLLM
        brain = BrainLLM(provider=provider)

        ctx = self.get_context()
        conns = self.get_connections()
        decisions = self.get_decisions(5)

        prompt = f"""You are auditing an AI Operating System workspace. Score each dimension 1-10 and give specific improvement actions.

WORKSPACE STATE:
- Context filled: {bool(ctx.get('identity'))}
- Connections registered: {len(conns)} ({sum(1 for c in conns if c.get('status') == 'connected')} active)
- Recent decisions: {len(decisions)}

FOUR-CS FRAMEWORK:
1. CONTEXT: Does the AI know who the user is, their business, priorities, and voice?
2. CONNECTIONS: What systems can the AI reach? Are key ones missing?
3. CAPABILITIES: What can the AI do for this user? What skills are missing?
4. CADENCE: Is there a regular practice of /audit and /level-up?

Respond ONLY with JSON:
{{
  "scores": {{"context": N, "connections": N, "capabilities": N, "cadence": N}},
  "overall": N,
  "gaps": ["specific gap 1", "specific gap 2", ...],
  "actions": ["do this first", "then this", ...],
  "summary": "one paragraph assessment"
}}"""

        try:
            response = await brain.stream_chat([{"role": "user", "content": prompt}])
            text = response.strip()
            for fence in ["```json", "```"]:
                text = text.replace(fence, "")
            result = json.loads(text.strip())
        except Exception as e:
            result = {
                "scores": {"context": 1, "connections": 1, "capabilities": 1, "cadence": 1},
                "overall": 1, "gaps": ["Workspace not onboarded yet"],
                "actions": ["Run /onboard first"],
                "summary": "Workspace is empty. Start with /onboard.",
                "error": str(e),
            }

        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        self._audit_file.write_text(json.dumps(result, indent=2))
        return result

    # ── /level-up skill ───────────────────────────────────────────────────────

    async def run_level_up(self, provider: Optional[str] = None) -> dict:
        """
        3Ms interview: find ONE automation to ship this week.
        AIS-OS /level-up skill.
        """
        from brain.llm import BrainLLM
        brain = BrainLLM(provider=provider)

        ctx = self.get_context()
        conns = self.get_connections()

        prompt = f"""You are running a weekly level-up interview to find ONE automation the user should build this week.

USER CONTEXT:
{json.dumps(ctx, indent=2)}

CONNECTED SYSTEMS:
{json.dumps([c['name'] for c in conns], indent=2)}

THREE Ms FRAMEWORK:
- Mindset: What assumption is limiting them?
- Method: What process could be improved?
- Machine: What can be automated?

CONSTRAINTS:
- Must be buildable in under 4 hours
- Must deliver measurable value
- Must use systems they already have connected

Respond ONLY with JSON:
{{
  "automation": "one sentence description of what to build",
  "why": "why this one, not something else",
  "steps": ["step 1", "step 2", "step 3"],
  "metric": "how to measure success",
  "estimated_hours": N,
  "skills_needed": ["python", "..."]
}}"""

        try:
            response = await brain.stream_chat([{"role": "user", "content": prompt}])
            text = response.strip()
            for fence in ["```json", "```"]:
                text = text.replace(fence, "")
            result = json.loads(text.strip())
        except Exception as e:
            result = {
                "automation": "Could not generate suggestion",
                "error": str(e),
            }

        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        self.log_decision(
            decision=f"Level-up target: {result.get('automation', '?')}",
            reasoning=result.get("why", ""),
        )
        return result

    def to_dict(self) -> dict:
        ctx = self.get_context()
        conns = self.get_connections()
        return {
            "user_id": self.user_id,
            "onboarded": bool(ctx.get("onboarded_at")),
            "context_summary": ctx.get("identity", "Not onboarded"),
            "connections": len(conns),
            "active_connections": sum(1 for c in conns if c.get("status") == "connected"),
        }
