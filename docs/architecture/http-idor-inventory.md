# HTTP Authorization Inventory (exhaustive discovery)

Total operations discovered: **295**

| Method | Path | Class | Family | Test status |
|--------|------|-------|--------|-------------|
| GET | `/api/account/avatar` | OWNER_SCOPED | resource | PASS |
| POST | `/api/account/avatar` | OWNER_SCOPED | resource | PASS |
| GET | `/api/account/avatar/{account_id}` | OWNER_SCOPED | resource | PASS |
| POST | `/api/account/bootstrap-owner` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/account/me` | OWNER_SCOPED | resource | PASS |
| POST | `/api/account/onboarding` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/account/plan` | OWNER_SCOPED | resource | PASS |
| PATCH | `/api/account/profile` | OWNER_SCOPED | resource | PASS |
| POST | `/api/agent/changes/{change_id}/accept` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/agent/changes/{change_id}/reject` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/agent/changes/{change_id}/revert` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/agent/patch/apply` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/agent/patch/preview` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/agent/run` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/agent/tasks` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/agent/tools` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/agent/{task_id}` | OWNER_SCOPED | resource | PASS |
| POST | `/api/agent/{task_id}/cancel` | OWNER_SCOPED | resource | PASS |
| GET | `/api/agent/{task_id}/changes` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/agent/{task_id}/changes/accept-all` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/agent/{task_id}/changes/reject-all` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/agent/{task_id}/events` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/auth/change-password` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/auth/delete-account` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/auth/login` | PUBLIC | auth-entry | PASS (public) |
| POST | `/api/auth/logout` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/auth/me` | AUTHENTICATED | api | PASS |
| POST | `/api/auth/supabase/exchange` | PUBLIC | auth-entry | PASS (public) |
| POST | `/api/auth/supabase/sync` | PUBLIC | auth-entry | PASS (public) |
| GET | `/api/capabilities` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/capabilities/categories` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/capabilities/export` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/capabilities/import` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/capabilities/{slug}` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/carai/health` | PUBLIC | health | PASS (public) |
| GET | `/api/carai/sessions` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/carai/sessions` | OWNER_SCOPED | resource | PASS |
| GET | `/api/carai/sessions/{session_id}` | OWNER_SCOPED | resource | PASS |
| POST | `/api/carai/sessions/{session_id}/status` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/carai/transcript` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/carai/transcript` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/chat/edit` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/chat/explain` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/chat/node-session` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/chat/send` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/chat/sessions` | OWNER_SCOPED | resource | PASS |
| DELETE | `/api/chat/sessions/{sid}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/chat/sessions/{sid}/messages` | OWNER_SCOPED | resource | PASS |
| GET | `/api/comms/stream` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/composer/apply` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/composer/execute` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/composer/plan` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/delivery/cloudflared/info` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/delivery/deploy/providers` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/delivery/plan` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/delivery/public/share/{share_id}` | PUBLIC | public-share | PASS |
| GET | `/api/delivery/saga/{saga_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| DELETE | `/api/delivery/shares/{share_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/delivery/tracing/health` | PUBLIC | health | PASS (public) |
| GET | `/api/delivery/tracing/{trace_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/delivery/tunnel/{tunnel_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/delivery/tunnel/{tunnel_id}/stop` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/delivery/{project_id}/delivery/run` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/delivery/{project_id}/deploy` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/delivery/{project_id}/runtime` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/delivery/{project_id}/runtime/logs` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/delivery/{project_id}/runtime/logs/recent` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/delivery/{project_id}/runtime/status` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/delivery/{project_id}/shares` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/delivery/{project_id}/tunnel/start` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/enterprise/audit` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/enterprise/audit/stats` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/enterprise/billing/events` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/enterprise/billing/usage` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/enterprise/marketplace` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/enterprise/marketplace/categories` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/enterprise/marketplace/{slug}` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/enterprise/rbac/check` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/enterprise/rbac/tiers` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/evidence/chains` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/evidence/chains/{chain_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/evidence/chains/{chain_id}/replay` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/evidence/chains/{chain_id}/stats` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/extras/agents` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/extras/agents/{slug}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/extras/autoresearch/sessions` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/extras/autoresearch/start` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/extras/build` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/extras/endpoints` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/extras/endpoints` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| DELETE | `/api/extras/endpoints/{eid}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/extras/endpoints/{eid}/models` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/extras/endpoints/{eid}/test` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/extras/projects` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| DELETE | `/api/extras/projects/{pid}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/extras/projects/{pid}/files` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/extras/stacks` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/extras/workspace` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/extras/workspace/audit` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/extras/workspace/connections` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/extras/workspace/connections` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/extras/workspace/context` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/extras/workspace/decisions` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/extras/workspace/decisions` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/extras/workspace/level-up` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/extras/workspace/onboard` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/files/{project_id}/app-detect` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/files/{project_id}/artifacts` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/files/{project_id}/create` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| DELETE | `/api/files/{project_id}/delete` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/files/{project_id}/download` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/files/{project_id}/export` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/files/{project_id}/list` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/files/{project_id}/preview` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/files/{project_id}/preview-readiness` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/files/{project_id}/preview-session` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/files/{project_id}/preview/{file_path:path}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/files/{project_id}/read` | OWNER_SCOPED | resource | PASS |
| POST | `/api/files/{project_id}/rename` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/files/{project_id}/secret-scan` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/files/{project_id}/tree` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/files/{project_id}/upload` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/files/{project_id}/upload-archive` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/files/{project_id}/upload-folder` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/files/{project_id}/write` | OWNER_SCOPED | resource | PASS |
| GET | `/api/governance/audit` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/governance/checkpoints` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/governance/hitl/history` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/governance/hitl/pending` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/governance/hitl/stats` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/governance/hitl/{request_id}/approve` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/governance/hitl/{request_id}/deny` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/governance/metrics` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/governance/tools` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/governance/traces` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/governance/traces/{trace_id}` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/governance/ucip/capabilities/{trust_level}` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/governance/ucip/identity` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/health` | PUBLIC | health | PASS (public) |
| GET | `/api/health/isolation` | PUBLIC | health | PASS (public) |
| GET | `/api/jobs` | OWNER_SCOPED | resource | PASS |
| POST | `/api/jobs` | OWNER_SCOPED | resource | PASS |
| GET | `/api/jobs/{job_id}` | OWNER_SCOPED | resource | PASS |
| POST | `/api/loop/run` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/loop/run/sync` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/lsp/status` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/marketplace/install` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/marketplace/search` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/marketplace/templates` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/marketplace/templates/categories` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/marketplace/templates/{template_id}` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/mcp/call` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/mcp/connect` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/mcp/disconnect/{name}` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/mcp/presets` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/mcp/servers` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/mcp/tools` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/memory/backend` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/memory/graph/entities` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/memory/graph/entity` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/memory/graph/entity/{entity_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/memory/graph/related/{entity_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/memory/graph/relationship` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/memory/graph/stats` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/memory/save` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/memory/search` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/models` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/models/complete` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/models/providers/config` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| PUT | `/api/models/providers/config` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/models/providers/test` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| DELETE | `/api/models/providers/{provider_id}/credential` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/models/providers/{provider_id}/credential` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| PUT | `/api/models/providers/{provider_id}/credential` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/models/settings` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/nodes/{node_id}/ai-action` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/orchestration` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/orchestration/detect-mode` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/orchestration/plan` | OWNER_SCOPED | resource | PASS |
| POST | `/api/orchestration/run` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/orchestration/{plan_id}` | OWNER_SCOPED | resource | PASS |
| POST | `/api/orchestration/{plan_id}/cancel` | OWNER_SCOPED | resource | PASS |
| GET | `/api/orchestration/{plan_id}/events` | OWNER_SCOPED | resource | PASS |
| POST | `/api/orchestration/{plan_id}/resume` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/personas` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/personas/classify` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/personas/default` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| PUT | `/api/personas/prefs` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/personas/xp-rules` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/personas/{persona_id}` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/personas/{persona_id}/accomplishments` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/personas/{persona_id}/experience` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/personas/{persona_id}/experience/events` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/personas/{persona_id}/learning` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/personas/{persona_id}/profile` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| PATCH | `/api/personas/{persona_id}/profile` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/personas/{persona_id}/system-prompt` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/ponytail/run` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/ponytail/runs` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/ponytail/runs/{run_id}` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/ponytail/stages` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/research/jobs` | OWNER_SCOPED | resource | PASS |
| GET | `/api/research/jobs/{job_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/research/quick` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/research/start` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/scripts` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/scripts` | OWNER_SCOPED | resource | PASS |
| POST | `/api/scripts/chains` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/scripts/chains/all` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| DELETE | `/api/scripts/chains/{chain_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| PATCH | `/api/scripts/chains/{chain_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/scripts/webhook/{token}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| DELETE | `/api/scripts/{sid}` | OWNER_SCOPED | resource | PASS |
| GET | `/api/scripts/{sid}` | OWNER_SCOPED | resource | PASS |
| PATCH | `/api/scripts/{sid}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/scripts/{sid}/ai-debug` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/scripts/{sid}/run` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/scripts/{sid}/runs` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/scripts/{sid}/webhook/rotate` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/search` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/search/files` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/search/index/reindex` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/search/index/status` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/secrets` | OWNER_SCOPED | resource | PASS |
| POST | `/api/secrets` | OWNER_SCOPED | resource | PASS |
| DELETE | `/api/secrets/{sid}` | OWNER_SCOPED | resource | PASS |
| GET | `/api/settings` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| PUT | `/api/settings` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/settings/account` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| PUT | `/api/settings/account` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/settings/ai` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| PUT | `/api/settings/ai` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/settings/appearance` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| PUT | `/api/settings/appearance` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/settings/assistant` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| PUT | `/api/settings/assistant` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/settings/models` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| PUT | `/api/settings/models` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/settings/providers` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| PUT | `/api/settings/providers` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/settings/workspace-layouts` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/settings/workspace-layouts` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| DELETE | `/api/settings/workspace-layouts/{layout_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/settings/workspace-layouts/{layout_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| PUT | `/api/settings/workspace-layouts/{layout_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/terminal/{project_id}/run` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/vcs/{project_id}/checkout` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/vcs/{project_id}/commit` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/vcs/{project_id}/diff` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/vcs/{project_id}/discard` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/vcs/{project_id}/init` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/vcs/{project_id}/log` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/vcs/{project_id}/pull` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/vcs/{project_id}/push` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/vcs/{project_id}/remote` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/vcs/{project_id}/remotes` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/vcs/{project_id}/stage` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/vcs/{project_id}/status` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/vcs/{project_id}/unstage` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/web-intel/fetch` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/web-intel/health` | PUBLIC | health | PASS (public) |
| GET | `/api/web/crawls` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/web/crawls` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/web/crawls/{crawl_id}` | OWNER_SCOPED | resource | PASS |
| POST | `/api/web/crawls/{crawl_id}/cancel` | OWNER_SCOPED | resource | PASS |
| GET | `/api/web/crawls/{crawl_id}/events` | OWNER_SCOPED | resource | PASS |
| GET | `/api/web/crawls/{crawl_id}/evidence` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/web/crawls/{crawl_id}/pages` | OWNER_SCOPED | resource | PASS |
| GET | `/api/web/crawls/{crawl_id}/report` | OWNER_SCOPED | resource | PASS |
| POST | `/api/web/crawls/{crawl_id}/resume` | OWNER_SCOPED | resource | PASS |
| POST | `/api/web/query` | AUTHENTICATED | api | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/workers` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/workers/plan/run` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/workers/{slug}` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/workers/{slug}/demote` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/workers/{slug}/promotion/approve` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/workers/{slug}/promotion/reject` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/workers/{slug}/run` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/workers/{slug}/run/sync` | AUTHENTICATED | catalog-or-admin | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/workflows` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/workflows` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/workflows/import` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/workflows/jobs/{job_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| DELETE | `/api/workflows/{workflow_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/workflows/{workflow_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| PATCH | `/api/workflows/{workflow_id}` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| POST | `/api/workflows/{workflow_id}/execute` | UCIP_GATED | execution-capable | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/workflows/{workflow_id}/export` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/api/workflows/{workflow_id}/ucip` | OWNER_SCOPED | resource | CLASSIFIED — not every verb dual-user exercised |
| GET | `/carai-agency-logo.png` | PUBLIC | PUBLIC | PASS (public) |
| GET | `/docs` | PUBLIC | PUBLIC | PASS (public) |
| GET | `/docs/oauth2-redirect` | PUBLIC | PUBLIC | PASS (public) |
| GET | `/openapi.json` | PUBLIC | PUBLIC | PASS (public) |
| GET | `/redoc` | PUBLIC | PUBLIC | PASS (public) |
| GET | `/{full_path:path}` | PUBLIC | SPA | PASS (public) |

## Counts

- **AUTHENTICATED**: 86
- **OWNER_SCOPED**: 180
- **PUBLIC**: 15
- **UCIP_GATED**: 14

## Unsupported

- `/api/missions/*`: UNSUPPORTED — NO ENDPOINT
- Supabase app tables: NOT HTTP-EXPOSED
