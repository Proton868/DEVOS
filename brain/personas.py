"""
Nuha Intelligence & Orchestration — Persona layer.

Personas are intelligence profiles over the EXISTING agent/capability stack.
They do NOT form a second execution, job, or authorization system.

Nuha is the default orchestrator: intent → plan → delegate → existing machinery
(UCIP → agent_runtime / workflow / workers).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from brain.agents import AGENT_LIBRARY, AgentPersona, get_agent


@dataclass
class Persona:
    id: str
    name: str
    description: str
    specialty: str
    system_prompt: str
    role: str = "specialist"  # orchestrator | specialist
    can_delegate: bool = False
    capabilities: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    creation_domains: list[str] = field(default_factory=list)
    advisory_domains: list[str] = field(default_factory=list)
    escalation_targets: list[str] = field(default_factory=list)
    # Map onto existing AgentPersona library when executing specialist work
    agent_slug: Optional[str] = None
    enabled_by_default: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "specialty": self.specialty,
            "role": self.role,
            "can_delegate": self.can_delegate,
            "capabilities": list(self.capabilities),
            "allowed_tools": list(self.allowed_tools),
            "creation_domains": list(self.creation_domains),
            "advisory_domains": list(self.advisory_domains),
            "escalation_targets": list(self.escalation_targets),
            "agent_slug": self.agent_slug,
            "enabled_by_default": self.enabled_by_default,
        }

    def bound_agent(self) -> Optional[AgentPersona]:
        if not self.agent_slug:
            return None
        return get_agent(self.agent_slug)


NUHA_SYSTEM_PROMPT = """You are Nuha — the primary intelligence and orchestration layer of DevOS.

ROLE
You interpret user intent and orchestrate existing DevOS machinery. You are NOT a separate execution engine.
You do NOT bypass UCIP governance. You do NOT claim work is done unless verification evidence exists.

INTENT CLASSES
Classify each request as one or more of:
CONVERSATION | ADVICE | CREATION | EXECUTION | AUTOMATION | CODE | RESEARCH | MULTI-DOMAIN

BEHAVIOR
- CONVERSATION / ADVICE: answer clearly as Nuha.
- CODE (user explicitly wants code snippets only): provide code in chat.
- CREATION / EXECUTION / AUTOMATION / MULTI-DOMAIN: do not merely dump code.
  Describe the plan, which specialists/capabilities are needed, and that execution
  goes through DevOS agent runtime + UCIP. Prefer actionable orchestration over
  passive HTML dumps when the user asked for a real artifact (e.g. a website).

DELEGATION
When a request spans domains, plan specialist hand-offs (Web, Code, Automation,
Design, Research, Data, Business). Specialists never receive more authority than you.
Escalate out-of-domain specialist work back to Nuha.

SAFETY
Your words are not authorization. File writes, installs, network, and irreversible
actions must pass existing UCIP / HITL gates. Prefer reversible steps first.

TONE
Direct, capable, Caribbean-proud professionalism. Ship-oriented. Honest about limits.
"""


# Specialist personas — thin profiles that bind to existing AGENT_LIBRARY entries
_SPECIALISTS: list[Persona] = [
    Persona(
        id="web",
        name="Web Specialist",
        description="Front-end pages, components, static sites, and web app UI.",
        specialty="web",
        role="specialist",
        system_prompt=(
            "You are the DevOS Web Specialist. Stay within web/frontend scope. "
            "If asked for legal, payments policy, or unrelated domains, escalate to Nuha."
        ),
        capabilities=["fs.write", "fs.read", "shell.exec"],
        allowed_tools=["write_file", "read_file", "run_terminal"],
        creation_domains=["website", "frontend", "ui", "react", "html"],
        advisory_domains=["css", "accessibility", "performance"],
        escalation_targets=["nuha"],
        agent_slug="frontend-developer",
    ),
    Persona(
        id="code",
        name="Code Specialist",
        description="General software engineering across backend and full-stack.",
        specialty="code",
        role="specialist",
        system_prompt=(
            "You are the DevOS Code Specialist. Build and modify software within "
            "engineering scope. Escalate pure design/legal/marketing work to Nuha."
        ),
        capabilities=["fs.write", "fs.read", "shell.exec"],
        allowed_tools=["write_file", "read_file", "run_terminal", "apply_patch"],
        creation_domains=["api", "service", "library", "refactor"],
        advisory_domains=["architecture", "testing"],
        escalation_targets=["nuha"],
        agent_slug="fullstack-engineer",
    ),
    Persona(
        id="automation",
        name="Automation Specialist",
        description="Workflows, triggers, scheduled jobs, and automation graphs.",
        specialty="automation",
        role="specialist",
        system_prompt=(
            "You are the DevOS Automation Specialist. Prefer the existing workflow "
            "engine. Do not invent a parallel automation runtime."
        ),
        capabilities=["workflow.write", "fs.write"],
        allowed_tools=["write_file", "run_terminal"],
        creation_domains=["workflow", "automation", "trigger", "pipeline"],
        advisory_domains=["orchestration", "ops"],
        escalation_targets=["nuha"],
        agent_slug="automation-engineer",
    ),
    Persona(
        id="design",
        name="Design Specialist",
        description="UI/UX guidance, visual hierarchy, and design systems advice.",
        specialty="design",
        role="specialist",
        system_prompt=(
            "You are the DevOS Design Specialist. Advise on UX/UI. Escalate pure "
            "backend or legal work to Nuha."
        ),
        capabilities=["fs.read"],
        allowed_tools=["read_file"],
        creation_domains=["design-system", "layout", "brand"],
        advisory_domains=["ux", "ui", "a11y"],
        escalation_targets=["nuha", "web"],
        agent_slug="ui-designer",
    ),
    Persona(
        id="research",
        name="Research Specialist",
        description="Web research, synthesis, and structured reports.",
        specialty="research",
        role="specialist",
        system_prompt=(
            "You are the DevOS Research Specialist. Use research tooling. "
            "Do not claim execution of builds or deploys."
        ),
        capabilities=["web.search", "fs.write"],
        allowed_tools=["search_web", "write_file"],
        creation_domains=["report", "brief"],
        advisory_domains=["research", "analysis"],
        escalation_targets=["nuha"],
        agent_slug="technical-writer",
    ),
    Persona(
        id="data",
        name="Data Specialist",
        description="Schemas, ETL, analytics, and data modeling.",
        specialty="data",
        role="specialist",
        system_prompt=(
            "You are the DevOS Data Specialist. Stay within data/engineering scope."
        ),
        capabilities=["fs.write", "fs.read", "shell.exec"],
        allowed_tools=["write_file", "read_file", "run_terminal"],
        creation_domains=["schema", "etl", "analytics"],
        advisory_domains=["sql", "modeling"],
        escalation_targets=["nuha"],
        agent_slug="data-engineer",
    ),
    Persona(
        id="business",
        name="Business Specialist",
        description="Product framing, go-to-market, and business analysis advice.",
        specialty="business",
        role="specialist",
        system_prompt=(
            "You are the DevOS Business Specialist. Advisory only for legal/finance "
            "edge cases — escalate formal legal or regulated advice to Nuha."
        ),
        capabilities=["fs.read"],
        allowed_tools=["read_file"],
        creation_domains=["brief", "prd"],
        advisory_domains=["product", "growth", "ops"],
        escalation_targets=["nuha"],
        agent_slug="product-manager",
    ),
]


NUHA = Persona(
    id="nuha",
    name="Nuha",
    description="Cross-domain intelligence and orchestration for DevOS.",
    specialty="orchestration",
    role="orchestrator",
    can_delegate=True,
    system_prompt=NUHA_SYSTEM_PROMPT,
    capabilities=["fs.read", "fs.write", "shell.exec", "web.search", "workflow.write"],
    allowed_tools=["*"],
    creation_domains=["*"],
    advisory_domains=["*"],
    escalation_targets=[],
    agent_slug=None,
    enabled_by_default=True,
)


PERSONA_REGISTRY: dict[str, Persona] = {NUHA.id: NUHA}
for _p in _SPECIALISTS:
    PERSONA_REGISTRY[_p.id] = _p


DEFAULT_PERSONA_ID = "nuha"


def get_persona(persona_id: str) -> Optional[Persona]:
    if not persona_id:
        return NUHA
    return PERSONA_REGISTRY.get(persona_id.lower()) or PERSONA_REGISTRY.get(persona_id)


def list_personas(*, enabled_only: bool = False) -> list[Persona]:
    items = list(PERSONA_REGISTRY.values())
    if enabled_only:
        items = [p for p in items if p.enabled_by_default]
    # Nuha first
    items.sort(key=lambda p: (0 if p.id == "nuha" else 1, p.name.lower()))
    return items


def resolve_system_prompt(persona_id: Optional[str] = None, extra: Optional[str] = None) -> str:
    persona = get_persona(persona_id or DEFAULT_PERSONA_ID) or NUHA
    prompt = persona.system_prompt
    if persona.agent_slug:
        agent = persona.bound_agent()
        if agent and agent.system_prompt:
            prompt = prompt + "\n\n--- Bound specialist profile ---\n" + agent.system_prompt
    if extra and str(extra).strip():
        prompt = prompt + "\n\n--- User extra instructions ---\n" + str(extra).strip()
    return prompt


def specialist_in_domain(persona_id: str, domain_hint: str) -> bool:
    p = get_persona(persona_id)
    if not p:
        return False
    if p.id == "nuha":
        return True
    hint = (domain_hint or "").lower()
    pool = [d.lower() for d in (p.creation_domains + p.advisory_domains + [p.specialty])]
    return any(d in hint or hint in d for d in pool if d != "*")


def suggest_personas_for_goal(goal: str) -> list[str]:
    """Lightweight keyword routing — planning still goes through Nuha + intent layer."""
    g = (goal or "").lower()
    scored: list[tuple[int, str]] = []
    for p in list_personas():
        if p.id == "nuha":
            continue
        score = 0
        for d in p.creation_domains + p.advisory_domains:
            if d and d != "*" and d.lower() in g:
                score += 2
        if p.specialty and p.specialty.lower() in g:
            score += 2
        if score:
            scored.append((score, p.id))
    scored.sort(reverse=True)
    return [pid for _, pid in scored[:5]]


# Intent classes Nuha uses when classifying (mirrors brief)
INTENT_CLASSES = (
    "CONVERSATION",
    "ADVICE",
    "CREATION",
    "EXECUTION",
    "AUTOMATION",
    "CODE",
    "RESEARCH",
    "PREVIEW",
    "MULTI-DOMAIN",
)


def classify_intent_heuristic(text: str) -> list[str]:
    t = (text or "").lower()
    classes: list[str] = []
    if any(k in t for k in ("build", "create", "make me", "scaffold", "generate a site", "website")):
        classes.append("CREATION")
    if any(k in t for k in ("run", "execute", "deploy", "install", "build the")):
        classes.append("EXECUTION")
    if any(k in t for k in ("workflow", "automat", "when someone", "trigger", "schedule")):
        classes.append("AUTOMATION")
    if any(k in t for k in ("research", "look up", "find sources", "summarize the web")):
        classes.append("RESEARCH")
    if any(k in t for k in ("only code", "snippet", "paste code")):
        classes.append("CODE")
    if any(k in t for k in (
        "preview", "show me the result", "show me what it looks like",
        "open the preview", "open the site", "view the page", "show the website",
        "show me the page", "open current project preview",
    )):
        classes.append("PREVIEW")
    if any(k in t for k in ("should i", "advise", "recommend", "what do you think")):
        classes.append("ADVICE")
    if not classes:
        classes.append("CONVERSATION")
    if len(classes) > 1:
        classes.append("MULTI-DOMAIN")
    # unique preserve order
    seen = set()
    out = []
    for c in classes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def should_orchestrate_execution(text: str) -> bool:
    """True when Nuha should prefer agent/workflow machinery over chat-only answers."""
    classes = set(classify_intent_heuristic(text))
    return bool(classes & {"CREATION", "EXECUTION", "AUTOMATION", "MULTI-DOMAIN"})


def surface_intent_for_message(text: str) -> dict:
    """
    Structured Surface Intent for Spatial Shell (not an execution grant).

    Derived from Nuha intent classification on the server — not a React heuristic.
    Does not authorize UCIP work.
    """
    classes = set(classify_intent_heuristic(text))
    # Default: stay in chat
    intent = {
        "surface": "chat",
        "action": "none",
        "required": False,
        "reason": "Conversational response",
        "confidence": 0.55,
        "context": {},
    }
    if classes & {"PREVIEW"} and not (classes & {"CREATION", "EXECUTION", "AUTOMATION"}):
        intent = {
            "surface": "preview",
            "action": "open",
            "required": True,
            "reason": "User requested workspace artifact preview",
            "confidence": 0.88,
            "context": {"filePath": "index.html"},
        }
    elif classes & {"AUTOMATION"}:
        intent = {
            "surface": "flow",
            "action": "open",
            "required": True,
            "reason": "Automation/workflow task — Flow surface",
            "confidence": 0.85,
            "context": {},
        }
    elif classes & {"CREATION", "EXECUTION"} and "CODE" not in classes:
        intent = {
            "surface": "ide",
            "action": "open",
            "required": True,
            "reason": "Creation/execution task — IDE surface",
            "confidence": 0.82,
            "context": {},
        }
    elif "CODE" in classes and not (classes & {"CREATION", "EXECUTION", "AUTOMATION"}):
        intent = {
            "surface": "chat",
            "action": "none",
            "required": False,
            "reason": "Code snippet request can stay in chat",
            "confidence": 0.7,
            "context": {},
        }
    elif "RESEARCH" in classes or "ADVICE" in classes or "CONVERSATION" in classes:
        intent = {
            "surface": "chat",
            "action": "none",
            "required": False,
            "reason": "Advisory/research conversation",
            "confidence": 0.75,
            "context": {},
        }
    intent["intent_classes"] = list(classes)
    return intent
