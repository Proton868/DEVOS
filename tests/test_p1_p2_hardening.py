
from governance.capability_registry import CapabilityDescriptor, CapabilityCategory
from governance.uci_interop import export_manifest, verify_manifest
from governance.agency_evolution import filter_autonomous_caps
from execution.isolation import IsolationResult, IsolationLevel

def test_hmac():
    c=CapabilityDescriptor(slug="t:x",name="x",category=CapabilityCategory.SYSTEM,description="")
    c.sign("k"); assert c.verify("k"); assert not c.verify("bad")

def test_uci():
    assert verify_manifest(export_manifest())

def test_gated():
    f=filter_autonomous_caps({"ucip:memory.read","ucip:system.shell"})
    assert "ucip:memory.read" in f and "ucip:system.shell" not in f

def test_iso():
    assert IsolationResult("ok","","",0,1,"unshare-net",IsolationLevel.ISOLATED.value).is_isolated
