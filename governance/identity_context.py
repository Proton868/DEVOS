"""IdentityContext as sole capability authority."""
from __future__ import annotations
import hashlib, hmac, json, os, time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Iterable
from governance.ucip import TrustLevel, AgentIdentity, TRUST_LEVEL_CAPS, ALWAYS_BLOCKED_CAPS

class ActorKind(str, Enum):
    HUMAN="human"; WORKER="worker"; SYSTEM="system"; SERVICE="service"
class TenantRole(str, Enum):
    OWNER="owner"; ADMIN="admin"; OPERATOR="operator"; MEMBER="member"; WORKER="worker"; SERVICE="service"
class AutonomyProfile(str, Enum):
    SUPERVISED="supervised"; BOUNDED="bounded"; AUTONOMOUS="autonomous"; FULL_AUTONOMOUS="full_autonomous"
class TenantTier(str, Enum):
    PUBLIC="public"; TENANT_USER="tenant_user"; TENANT_ADMIN="tenant_admin"
    AGENCY_OP="agency_operator"; SYSTEM="system"

def _key():
    return (os.environ.get("DEVOS_CAPABILITY_TOKEN_SECRET") or os.environ.get("SECRET_KEY") or "devos-dev-token-secret").encode()
def _cjson(o): return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str)

@dataclass
class CapabilityToken:
    token_id: str; caps: list[str]; issued_by: str
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime]=None; evidence_ref: Optional[str]=None; signature: Optional[str]=None
    def is_expired(self):
        return self.expires_at is not None and datetime.now(timezone.utc) > self.expires_at
    def _payload(self):
        return _cjson({"token_id":self.token_id,"caps":sorted(self.caps),"issued_by":self.issued_by,
            "issued_at":self.issued_at.isoformat() if self.issued_at else None,
            "expires_at":self.expires_at.isoformat() if self.expires_at else None,"evidence_ref":self.evidence_ref})
    def sign(self, secret=None):
        self.signature = hmac.new(secret or _key(), self._payload().encode(), hashlib.sha256).hexdigest(); return self.signature
    def verify(self, secret=None):
        if not self.signature: return False
        return hmac.compare_digest(self.signature, hmac.new(secret or _key(), self._payload().encode(), hashlib.sha256).hexdigest())
    def to_dict(self):
        return {"token_id":self.token_id,"caps":self.caps,"issued_by":self.issued_by,"issued_at":self.issued_at.isoformat(),
            "expires_at":self.expires_at.isoformat() if self.expires_at else None,"evidence_ref":self.evidence_ref,"signature":self.signature}
    @classmethod
    def issue(cls, caps, issued_by="system", expires_at=None, evidence_ref=None, token_id=None):
        tid = token_id or ("tok:"+hashlib.sha256(f"{issued_by}:{time.time()}:{sorted(caps)}".encode()).hexdigest()[:20])
        t=cls(token_id=tid,caps=list(caps),issued_by=issued_by,expires_at=expires_at,evidence_ref=evidence_ref); t.sign(); return t

def _trust_to_auto(t):
    if t>=TrustLevel.ROOT: return AutonomyProfile.FULL_AUTONOMOUS
    if t>=TrustLevel.AUTONOMOUS: return AutonomyProfile.AUTONOMOUS
    if t>=TrustLevel.OPERATOR: return AutonomyProfile.BOUNDED
    return AutonomyProfile.SUPERVISED

@dataclass
class IdentityContext:
    actor_id: str; tenant_id: str; trust_tier: TenantTier
    actor_kind: ActorKind=ActorKind.HUMAN; tenant_role: TenantRole=TenantRole.MEMBER
    trust_level: TrustLevel=TrustLevel.OPERATOR; autonomy: AutonomyProfile=AutonomyProfile.BOUNDED
    capability_tokens: list=field(default_factory=list); expected_outcome_schema: Optional[dict]=None
    delegation_chain: list=field(default_factory=list); agent_identity: Optional[AgentIdentity]=None
    session_id: Optional[str]=None
    request_id: str=field(default_factory=lambda: hashlib.sha256(f"{time.time()}".encode()).hexdigest()[:16])
    @classmethod
    def create(cls, actor_id, tenant_id, *, actor_kind=ActorKind.HUMAN, tenant_role=TenantRole.MEMBER,
               trust_level=TrustLevel.OPERATOR, trust_tier=None, is_platform_admin=False, caps=None, session_id=None,
               delegation_chain=None, expected_outcome_schema=None):
        tier = trust_tier or (TenantTier.SYSTEM if actor_kind==ActorKind.SYSTEM else
            (TenantTier.AGENCY_OP if is_platform_admin and actor_kind==ActorKind.HUMAN else TenantTier.TENANT_USER))
        granted = set(caps) if caps is not None else set(TRUST_LEVEL_CAPS.get(trust_level, set()))
        granted -= set(ALWAYS_BLOCKED_CAPS)
        tok = CapabilityToken.issue(sorted(granted), "system", token_id=f"ctx:{actor_id}:{session_id or 's'}")
        return cls(actor_id=actor_id, tenant_id=tenant_id, trust_tier=tier, actor_kind=actor_kind, tenant_role=tenant_role,
            trust_level=trust_level, autonomy=_trust_to_auto(trust_level), capability_tokens=[tok],
            expected_outcome_schema=expected_outcome_schema, delegation_chain=list(delegation_chain or []), session_id=session_id)
    @classmethod
    def from_agent(cls, agent, tenant_id="default", trust_tier=None, actor_kind=ActorKind.HUMAN,
                   tenant_role=TenantRole.MEMBER, is_platform_admin=False):
        ctx = cls.create(agent.agent_id, tenant_id, actor_kind=actor_kind, tenant_role=tenant_role,
            trust_level=agent.trust_level, trust_tier=trust_tier, is_platform_admin=is_platform_admin,
            caps=list(agent.capabilities), session_id=agent.session_id, delegation_chain=list(agent.delegation_chain))
        ctx.agent_identity = agent; return ctx
    def effective_capabilities(self):
        caps=set()
        for t in self.capability_tokens:
            if t.is_expired(): continue
            if t.signature is not None and not t.verify(): continue
            caps.update(t.caps)
        return caps
    def has_cap(self, cap):
        g=self.effective_capabilities(); return "*" in g or cap in g
    def can_act_as(self, required):
        o={TenantTier.PUBLIC:0,TenantTier.TENANT_USER:1,TenantTier.TENANT_ADMIN:2,TenantTier.AGENCY_OP:3,TenantTier.SYSTEM:4}
        return o.get(self.trust_tier,0)>=o.get(required,0)
    def is_platform_operator(self): return self.trust_tier in (TenantTier.AGENCY_OP, TenantTier.SYSTEM)
    def is_autonomous_worker(self):
        return self.actor_kind==ActorKind.WORKER and self.autonomy in (AutonomyProfile.AUTONOMOUS, AutonomyProfile.FULL_AUTONOMOUS)
    def to_dict(self):
        return {"actor_id":self.actor_id,"tenant_id":self.tenant_id,"trust_tier":self.trust_tier.value,
            "actor_kind":self.actor_kind.value,"tenant_role":self.tenant_role.value,
            "trust_level":self.trust_level.name.lower(),"autonomy":self.autonomy.value,
            "capability_tokens":[t.to_dict() for t in self.capability_tokens],"delegation_chain":self.delegation_chain,
            "session_id":self.session_id,"request_id":self.request_id}
    def to_agent_identity(self):
        if self.agent_identity is not None: return self.agent_identity
        a=AgentIdentity.create(self.actor_id, self.session_id or "session", trust_level=self.trust_level, extra_caps=None)
        a.capabilities=self.effective_capabilities(); a.delegation_chain=list(self.delegation_chain)
        self.agent_identity=a; return a

def resolve_identity(user_id, session_id, tenant_id="default", trust_level=TrustLevel.OPERATOR, *,
    actor_kind=ActorKind.HUMAN, tenant_role=TenantRole.MEMBER, is_platform_admin=False, caps=None):
    return IdentityContext.create(user_id, tenant_id, actor_kind=actor_kind, tenant_role=tenant_role,
        trust_level=trust_level, is_platform_admin=is_platform_admin, caps=caps, session_id=session_id)

def resolve_worker_identity(worker_id, session_id, tenant_id, trust_level=TrustLevel.AUTONOMOUS,
    tenant_role=TenantRole.WORKER, caps=None):
    return IdentityContext.create(worker_id, tenant_id, actor_kind=ActorKind.WORKER, tenant_role=tenant_role,
        trust_level=trust_level, is_platform_admin=False, caps=caps, session_id=session_id)
