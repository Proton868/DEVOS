from __future__ import annotations
from datetime import datetime, timezone
from governance.identity_context import AutonomyProfile
from governance.ucip import TrustLevel
_PROMOTE=[(50,0.15,0,"operator",AutonomyProfile.BOUNDED.value),(500,0.05,0,"autonomous",AutonomyProfile.AUTONOMOUS.value),(4000,0.01,0,"autonomous",AutonomyProfile.FULL_AUTONOMOUS.value)]
ALWAYS_HUMAN_GATED={"ucip:system.shell","ucip:secret.read","ucip:filesystem.delete","ucip:vcs.push","financial.transfer","governance.change"}
async def get_or_create_trust(db, tenant_id, worker_id):
    from sqlalchemy import select
    from core.database import WorkerTrustRecord, gen_id
    r=await db.execute(select(WorkerTrustRecord).where(WorkerTrustRecord.tenant_id==tenant_id, WorkerTrustRecord.worker_id==worker_id))
    row=r.scalar_one_or_none()
    if row: return row
    row=WorkerTrustRecord(id=gen_id(), tenant_id=tenant_id, worker_id=worker_id, trust_level="supervised",
        autonomy=AutonomyProfile.SUPERVISED.value, granted_caps=[], evidence={})
    db.add(row); await db.commit(); await db.refresh(row); return row
async def record_outcome(db, tenant_id, worker_id, *, success, unauthorized=False, capability=None):
    row=await get_or_create_trust(db, tenant_id, worker_id)
    if success: row.success_count=(row.success_count or 0)+1
    else: row.failure_count=(row.failure_count or 0)+1
    if unauthorized: row.unauthorized_attempts=(row.unauthorized_attempts or 0)+1
    total=(row.success_count or 0)+(row.failure_count or 0)
    if total:
        fr=(row.failure_count or 0)/total; unauth=row.unauthorized_attempts or 0
        tt,ta=row.trust_level,row.autonomy
        for min_s,max_fr,max_u,tl,auto in _PROMOTE:
            if row.success_count>=min_s and fr<=max_fr and unauth<=max_u: tt,ta=tl,auto
        if unauth>0 or fr>0.3: tt,ta="supervised",AutonomyProfile.SUPERVISED.value
        row.trust_level,row.autonomy=tt,ta
    row.updated_at=datetime.now(timezone.utc); await db.commit(); await db.refresh(row); return row
def filter_autonomous_caps(caps): return {c for c in caps if c not in ALWAYS_HUMAN_GATED}
