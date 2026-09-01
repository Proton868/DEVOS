from __future__ import annotations
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from core.database import DurableCapability, gen_id
from governance.capability_registry import CapabilityDescriptor, CapabilityCategory, CapabilityRisk, get_registry
logger = logging.getLogger("devos.durable_caps")

def descriptor_from_row(row):
    body=dict(row.body or {})
    try: category=CapabilityCategory(body.get("category") or row.category or "system")
    except ValueError: category=CapabilityCategory.SYSTEM
    try: risk_e=CapabilityRisk(body.get("risk") or row.risk or "medium")
    except ValueError: risk_e=CapabilityRisk.MEDIUM
    return CapabilityDescriptor(slug=row.slug, name=row.name or row.slug, category=category,
        description=row.description or "", risk=risk_e, input_schema=body.get("input_schema") or {},
        output_schema=body.get("output_schema") or {}, trust_required=body.get("trust_required") or "operator",
        timeout_s=int(body.get("timeout_s") or 30), max_retries=int(body.get("max_retries") or 1),
        is_reversible=bool(body.get("is_reversible", True)), requires_hitl=bool(body.get("requires_hitl", False)),
        requires_network=bool(body.get("requires_network", False)), version=row.version or "1.0.0",
        signature=row.signature, metadata={**(body.get("metadata") or {}), "tenant_id": row.tenant_id})

async def load_tenant_capabilities(db, tenant_id=None):
    q=select(DurableCapability).where(DurableCapability.is_active==True, DurableCapability.approval_state=="approved")
    if tenant_id: q=q.where((DurableCapability.tenant_id==tenant_id)|(DurableCapability.tenant_id.is_(None)))
    r=await db.execute(q); reg=get_registry(); n=0
    for row in r.scalars().all():
        try: reg.register(descriptor_from_row(row)); n+=1
        except Exception as e: logger.warning("%s", e)
    return n

async def persist_capability(db, descriptor, *, tenant_id=None, owner_id=None, approval_state="approved"):
    r=await db.execute(select(DurableCapability).where(DurableCapability.slug==descriptor.slug, DurableCapability.tenant_id==tenant_id))
    row=r.scalar_one_or_none(); body=descriptor.to_dict(); now=datetime.now(timezone.utc)
    if row is None:
        row=DurableCapability(id=gen_id(), tenant_id=tenant_id, owner_id=owner_id, slug=descriptor.slug,
            version=descriptor.version, name=descriptor.name, category=descriptor.category.value,
            description=descriptor.description, risk=descriptor.risk.value, body=body,
            signature=descriptor.signature, approval_state=approval_state, is_active=True)
        db.add(row)
    else:
        row.version=descriptor.version; row.name=descriptor.name; row.body=body; row.signature=descriptor.signature
        row.approval_state=approval_state; row.is_active=True; row.updated_at=now
    await db.commit(); await db.refresh(row); get_registry().register(descriptor); return row
