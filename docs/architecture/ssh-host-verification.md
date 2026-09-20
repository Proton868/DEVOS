# SSH host identity verification

## States

| State | Meaning | Agent | Human |
|-------|---------|-------|-------|
| `NEW_HOST` | No known fingerprint | Pause — approval required | TOFU or pin after display |
| `KNOWN_HOST` | Fingerprint matches | Allowed | Allowed |
| `CHANGED_HOST` | Fingerprint differs | **HARD STOP** | Investigate; explicit replace + MITM ack |
| `REVOKED_HOST` | Fingerprint revoked | **HARD STOP** | Re-pin only via replace flow |

## Non-negotiables

- No `StrictHostKeyChecking=no` (or equivalent) anywhere.
- Agents never silently approve NEW or CHANGED hosts.
- Host identity (hostname, port, key type, SHA256 fingerprint, trust state) is attached to execution evidence.

## Flows

```
handshake presents host key
        ↓
verify_host_key(owner, host_identity, presented)
        ↓
assert_connect_allowed → open channels
```

Human TOFU: `approve_new_host(..., trust_state=tofu|pinned)`.  
After CHANGED: `replace_changed_host_key(..., acknowledge_mitm_risk=True)`.

## Transport

`execution/ssh_transport.py` uses **asyncssh** when installed; tests use `MockSshBackend`.
Business logic uses `SSHConnectionCapability` / `SSHExecCapability` / `SSHSessionCapability`.
