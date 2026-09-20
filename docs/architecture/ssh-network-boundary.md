# SSH Network Boundary Policy

## Goal

SSH must not become an unrestricted network pivot for agents or compromised workspaces.

## Target classes

| Class | Default agent | Default user | Notes |
|-------|---------------|--------------|-------|
| Public internet | Allow (optional allowlist) | Allow | DNS-resolved public IPs only |
| Private RFC1918 | Deny | Deny unless `DEVOS_SSH_ALLOW_PRIVATE_NETWORKS=1` or allowlist | 10/8, 172.16/12, 192.168/16 |
| Localhost / loopback | Deny | Deny unless `DEVOS_SSH_ALLOW_LOCALHOST=1` | 127.0.0.0/8, ::1 |
| Link-local | Deny | Deny | 169.254.0.0/16 (non-metadata) |
| Cloud metadata | Deny | Deny | 169.254.169.254, GCP metadata host |
| Unix sockets | Deny | Deny | path/`unix:` URIs |
| Internal `.local`/`.internal` | Deny as internal | Same | Treated as internal service |

## Resolution rules

1. Do not trust hostname strings alone.
2. Resolve **all** A/AAAA addresses.
3. If **any** address is blocked, deny (DNS rebinding defense).
4. Fail closed on DNS failure.

## Environment overrides

- `DEVOS_SSH_ALLOW_PRIVATE_NETWORKS=1` — managed private fleets
- `DEVOS_SSH_ALLOW_LOCALHOST=1` — local dev only
- `DEVOS_SSH_HOST_ALLOWLIST=host1,host2` — comma-separated hosts
- `DEVOS_SSH_AGENT_REQUIRE_ALLOWLIST=1` — agents need allowlist even for public hosts

## Git SSH

Same network policy applies to `git@host:path` and `ssh://` remotes.
Git credentials are **not** server SSH credentials unless explicitly linked.
