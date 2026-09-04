# Canonical DevOS Identity Contract

See also: [identity-threat-model.md](identity-threat-model.md)

## Code

`governance/identity_contract.py` — `DevOSIdentity`, ownership assert, client field stripping.

## Flow

Authenticated session → `users` row (account_id) → optional tenant membership → AgentIdentity for UCIP → execution.

**Never trust:** request body `user_id`, `account_id`, `role`, `plan`, `is_admin`, URL-scoped foreign account ids without ownership verification.

## Avatar

Avatar upload via FileService (`write_bytes` under `{user_id}/profile/`). Foreign account_id → 404.
