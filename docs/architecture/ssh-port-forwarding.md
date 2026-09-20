# SSH Port Forwarding Policy

Isolation > completeness.

## Default

**All forwarding DENIED** (local, remote, SOCKS).

Tunnels are not opened unless:

1. `user_confirmed=true`
2. Destination passes network boundary policy
3. Destination is on an explicit allowlist
4. Local forward binds loopback only
5. `DEVOS_SSH_ENABLE_PORT_FORWARD=1` is set for the deployment

SOCKS is not enabled in this release even with confirmation.

## Audit record fields

source, destination, port, user, workspace, agent, duration, approval, connection_id
