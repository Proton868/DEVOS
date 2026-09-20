# Agentic SSH Capability Matrix

Baselines: Termius-class client, VS Code Remote, Cursor agentic, n8n, PyRunner, Relevance-style tools.

| CAPABILITY | IMPLEMENTED? | PRODUCTION-READY? | TESTED? | EVIDENCE? | SECURITY RISK? | LIMITATION? | NEXT ACTION? |
|------------|--------------|-------------------|---------|-----------|----------------|-------------|--------------|
| SSH connections | Yes | Beta | Yes | Yes | Low | Thin CRUD UI | API polish |
| Host management | Partial | No | Partial | UI | Low | Tags not durable | Persist favorites |
| Authentication | Yes | Beta | Yes | Yes | Medium | — | HSM later |
| Host-key verification | Yes | Yes | Yes | Yes | Critical if bypass | — | Keep strict |
| Interactive terminal | Partial | No | Partial | WS | Medium | PTY incomplete | Live PTY CI |
| PTY | Partial | No | Unit | API | Medium | Mock-first | asyncssh loop |
| Multiple sessions | Partial | No | Partial | Map | Low | UX tabs | Spatial tabs |
| File transfer | Yes | Beta | Yes | Resume | Medium | Mock SFTP | Live SFTP |
| Remote filesystem | Partial | No | Partial | Pocket | Medium | Thin browse | Wire LIST UI |
| Remote diagnostics | Yes | Beta | Yes | Caps | Low | — | Expand parsers |
| Process management | Inspect | Beta | Yes | Yes | — | No kill | Governed kill |
| Service management | Inspect+plan | Beta | Yes | Yes | High | Approval required | — |
| Logs | Yes | Beta | Yes | Yes | Low | — | — |
| Docker management | Inspect | Beta | Yes | Yes | Medium | Read-only | Approve writes |
| Git over SSH | Auth+mock | No | Yes | Yes | High | No default live runner | Optional runner |
| Agentic commands | Yes | Beta | Yes | Yes | High | — | — |
| Multi-step execution | Yes | Beta | Yes | Yes | High | — | — |
| Planning | Yes | Beta | Yes | Yes | Medium | Heuristic NL | LLM plans |
| Approval | Yes | Yes | Yes | Yes | — | — | — |
| Verification | Yes | Beta | Yes | Yes | Medium | Heuristic | Stronger health |
| Cancellation | Yes | Beta | Yes | Yes | Medium | Live pg untested | SSHD test |
| Reconnection | Yes | Beta | Yes | Yes | High | — | — |
| Idempotency | Policy | Beta | Yes | Yes | High | — | — |
| Evidence | Yes | Beta | Yes | Yes | — | Process-local | Durable store |
| Credential isolation | Yes | Yes | Yes | Yes | Critical | — | — |
| Workspace isolation | Yes | Beta | Yes | Yes | Medium | — | — |
| User isolation | Yes | Yes | Yes | Yes | Critical | — | — |
| Network isolation | Yes | Yes | Yes | Yes | Critical | — | — |
| Resource limits | Yes | Beta | Yes | Yes | Medium | — | — |
| Prompt-injection resistance | Yes | Beta | Yes | Yes | High | — | Expand patterns |
| Port forwarding | Deny | N/A | Yes | Yes | Critical if on | Disabled | Keep denied |
| Deployment workflows | Partial | No | Partial | Plan | High | Placeholders | Playbooks |
| Rollback | Partial | No | Partial | Intent | High | Not proven | Careful impl |
| Recovery | Partial | Beta | Yes | UNKNOWN | High | Partial | Job linkage |
| Auditability | Yes | Beta | Yes | Yes | — | — | — |
| Server Operator NL | Yes | Beta | Yes | Yes | High | Heuristic | Expand intents |

## Next implementation sequence
1. Disposable SSHD CI (PTY + SFTP)
2. Durable SSH evidence in Postgres
3. Production smoke with identity hard-stop
4. Real deploy/rollback playbooks
5. Persist host manager metadata
6. Keep port forwarding disabled
