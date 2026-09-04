# Artifact → Build → Verify → Preview → Share → GitHub → Deploy

## Authority chain (invariant)

```
Nuha → Mission/DAG → Specialty Policy → UCIP → Agent Runtime
  → Workspace/FileService → Verification → Evidence
```

External side effects (GitHub push, deploy, public publish, tunnels) remain **UCIP-gated**.

## Artifact foundation (Phase 1)

- Upload / multi-file / archive extract via `execution.artifacts`
- Path authority: `FileService._resolve` only
- Zip-slip, symlink, absolute paths, depth/size budgets rejected
- Export ZIP excludes secrets by default
- App detection: `execution.app_detect` (STATIC / NEXTJS / VITE / REACT / NODE / PYTHON)

## Preview security (existing)

- Short-lived `typ=devos_preview` credentials
- Opaque iframe (`sandbox=allow-scripts`, no `allow-same-origin`)
- CSP: `connect-src 'none'`, no `wasm-unsafe-eval` on primary origin
- Dedicated preview origin recommended for full WASM/Next.js (Phase 2/3)

## Application Runtime vs Agent Runtime

| | Agent Runtime | Application Runtime |
|--|---------------|---------------------|
| Purpose | AI tools | User app preview |
| Credentials | May use user provider keys via vault | **Never** DevOS/provider secrets |
| Network | UCIP-governed tools | Default deny / limited |

## Deployment adapters (Phase 6 scaffold)

`execution.deploy` registry: `vercel`, `netlify`, `cloudflare_tunnel`.

Live deploy **fails closed** without credentials (`DEPLOYMENT_AUTH_REQUIRED`).

## GitHub

Existing `execution.vcs.GitService` + `api/routes/vcs.py` + GitPanel.

Push remains EXTERNAL_SIDE_EFFECT — do not bypass UCIP.

## Lifecycle states (derived)

`IMPORTED → WORKING → BUILT → VERIFIED → READY → PREVIEWING → SHARED → DEPLOYED → PUBLISHED`

Derived from workspace + verification + preview readiness + git + deployment evidence — not a second authority.
