# Canonical DevOS Identity Contract

See also: [identity-threat-model.md](identity-threat-model.md)

## Code

`governance/identity_contract.py` — `DevOSIdentity`, ownership assert, client field stripping.

## Flow

Authenticated session → `users` row (account_id) → optional tenant membership → AgentIdentity for UCIP → execution.

**Never trust:** request body `user_id`, `account_id`, `role`, `plan`, `is_admin`, URL-scoped foreign account ids without ownership verification.

## Avatar

URL field only (`avatar_url`). Upload via FileService is **not** implemented.
