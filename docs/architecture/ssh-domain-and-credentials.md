# SSH domain model and secure credentials

## Authority

- **Ownership:** every SSH row is `owner_id`-scoped (optional `tenant_id` / `workspace_id`).
- **Secrets:** private keys, passwords, and passphrases exist only in `secrets.encrypted_value` (Fernet via `governance/secrets_vault.py`).
- **Handles:** `ssh_credential_refs` stores `secret_id` references and public metadata only.
- **RLS:** migration enables owner policies consistent with existing tenant isolation patterns.

## Tables

| Table | Purpose |
|-------|---------|
| `ssh_host_identities` | Canonical hostname/IP + port |
| `ssh_credential_refs` | Auth method + secret refs (revocable) |
| `ssh_known_hosts` | Fingerprints / public keys |
| `ssh_connections` | User connection profiles |
| `ssh_sessions` | Session lifecycle (no key material) |
| `ssh_execution_records` | Command runs + evidence refs |
| `ssh_file_transfer_jobs` | SFTP job metadata |

## Resolution path

```
Agent/API → credential_ref_id (handle)
         → owner + !revoked check
         → decrypt in process memory (transport only)
         → clear() after use
```

Public serializers never include `secret_id`, ciphertext, or PEM material.
