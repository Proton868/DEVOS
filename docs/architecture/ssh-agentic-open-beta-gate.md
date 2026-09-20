# Agentic SSH Open Beta Gate

## Gate results

| Gate | Result | Notes |
|------|--------|-------|
| A. SERVER IDENTITY | PASS (offline) / UNTESTED (live) | Fingerprint required; live needs DEVOS_SSH_PROD_* |
| B. DEPLOYMENT IDENTITY | UNTESTED live | Offline readiness PASS |
| C. AUTHENTICATION | PASS | Owner-scoped credentials |
| D. AUTHORIZATION | PASS | UCIP + policy + ownership |
| E. HOST KEY VERIFICATION | PASS | Agent cannot TOFU; CHANGED hard stop |
| F. CREDENTIAL ISOLATION | PASS | No material in API/LLM/audit |
| G. USER ISOLATION | PASS | Cross-user denied |
| H. WORKSPACE ISOLATION | PASS | Path confinement |
| I. NETWORK BOUNDARIES | PASS | Metadata/private/localhost denied |
| J. COMMAND GOVERNANCE | PASS | Risk classes + confirmation |
| K. AGENT GOVERNANCE | PASS | Operator approval gates |
| L. PROMPT-INJECTION RESISTANCE | PASS | Untrusted remote content |
| M. FILESYSTEM SAFETY | PASS | Traversal/symlink/size |
| N. PROCESS CLEANUP | PASS (mock) / UNTESTED live PTY | Cancel closes transport |
| O. RESOURCE LIMITS | PASS | Timeouts, clamps, rate |
| P. CANCELLATION | PASS | Never success |
| Q. RECONNECTION | PASS | Host-key change fails closed |
| R. IDEMPOTENCY | PASS | Unknown → no blind retry |
| S. EVIDENCE | PASS | Chain + grounded claims |
| T. AUDITABILITY | PASS | Structured audit |
| U. DISASTER RECOVERY | PASS partial | UNKNOWN surfaced |
| V. END-TO-END AGENT | PASS (mock) | Operator + workflow |

## Open beta decision

**OPEN BETA: PERMITTED** for controlled/mock-backed environments.

Critical security failures in automated suite: **none** (no credential leak, cross-user escape, authz bypass, silent host-key accept).

### Documented limitations
1. Live SSHD CI optional for beta
2. Live PTY process-group kill untested
3. Git live runner optional
4. Port forwarding disabled
5. Production live identity UNTESTED until env configured


## Gate verification stamp

Automated suite (tests/test_ssh_*.py + structured_audit): green on commit following Server Operator + failure matrix expansion.
OPEN BETA remains PERMITTED under documented limitations.
