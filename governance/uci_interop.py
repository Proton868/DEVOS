from __future__ import annotations
import hashlib, hmac, os, json
from datetime import datetime, timezone
from governance.capability_registry import CapabilityDescriptor, get_registry, CapabilityCategory, CapabilityRisk
def _cjson(o): return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str)
def _key(): return (os.environ.get("DEVOS_UCI_INTEROP_SECRET") or os.environ.get("SECRET_KEY") or "devos-uci-interop").encode()
def export_manifest(slugs=None):
    caps=get_registry().list_all()
    if slugs: caps=[c for c in caps if c.slug in set(slugs)]
    payload={"uci_version":"1.0","exported_at":datetime.now(timezone.utc).isoformat(),"capabilities":[c.to_dict() for c in caps],"count":len(caps)}
    return {"manifest":payload,"signature":hmac.new(_key(),_cjson(payload).encode(),hashlib.sha256).hexdigest(),"alg":"HMAC-SHA256"}
def verify_manifest(envelope):
    m,s=envelope.get("manifest"),envelope.get("signature")
    if not m or not s: return False
    return hmac.compare_digest(s, hmac.new(_key(),_cjson(m).encode(),hashlib.sha256).hexdigest())
def import_manifest(envelope, *, require_signature=True):
    if require_signature and not verify_manifest(envelope): raise ValueError("invalid UCI interop signature")
    imported=[]; reg=get_registry()
    for item in (envelope.get("manifest") or {}).get("capabilities") or []:
        try:
            desc=CapabilityDescriptor(slug=item["slug"], name=item.get("name") or item["slug"],
                category=CapabilityCategory(item.get("category","system")), description=item.get("description") or "",
                risk=CapabilityRisk(item.get("risk","medium")), input_schema=item.get("input_schema") or {},
                output_schema=item.get("output_schema") or {}, version=item.get("version") or "1.0.0",
                metadata={**(item.get("metadata") or {}), "imported": True})
            reg.register(desc); imported.append(desc)
        except Exception: continue
    return imported
