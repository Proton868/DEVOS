# schema_migrations history — operator reconciliation

**Audience:** human operator on production (`prime`).  
The coding agent must **not** delete `schema_migrations` rows automatically.

## What happened

The migration runner previously discovered every `supabase/migrations/*.sql` file,
including rollback companions named `*.down.sql`.

On production, `schema_migrations` therefore contains a historical extra row:

```
20260918200000_agentic_runtime_checkpoints.down.sql
```

in addition to the legitimate forward file:

```
20260918200000_agentic_runtime_checkpoints.sql
```

Lexical order is `.down.sql` **before** `.sql`. If both were applied in that
order on first install:

1. The rollback file ran against a **missing** table (`DROP … IF EXISTS` no-op).
2. The forward file then created `agentic_runtime_checkpoints`.

That accidental apply is why the extra row exists. **Do not treat the extra
row as a license to re-run the rollback.** Re-applying the `.down.sql` file
would `DROP TABLE agentic_runtime_checkpoints` and destroy checkpoint rows.

## Code fix (already in the repository)

`scripts/apply_supabase_migrations.py` now:

- applies **only** forward migrations (`YYYYMMDDHHMMSS_name.sql`)
- **never** selects `*.down.sql`, `*.rollback.sql`, `*.backup.sql`, `*.seed.sql`

The rollback file remains in git for documented manual undo. It will not
enter `schema_migrations` again on a fresh database.

## Production remediation — do NOT auto-delete the row

Leaving the historical row in place is the **safest** option:

- It documents that the file was once executed.
- Deleting it would let a **naive** future runner (old code) apply
  `DROP TABLE` as if it were pending.

### Operator checklist (manual)

1. Confirm the table still exists:

   ```sql
   SELECT to_regclass('public.agentic_runtime_checkpoints');
   ```

   Expected: `agentic_runtime_checkpoints`

2. Confirm both rows exist (informational only):

   ```sql
   SELECT filename, applied_at
   FROM schema_migrations
   WHERE filename LIKE '20260918200000_agentic_runtime_checkpoints%'
   ORDER BY filename;
   ```

3. **Do not** `DELETE FROM schema_migrations WHERE filename LIKE '%.down.sql'`.

4. Deploy the fixed runner, then apply **forward** migrations only:

   ```bash
   bash ops/apply_migrations.sh
   ```

5. After deploy, pending forwards such as
   `20261006220000_rls_world_isolation_completion.sql` should apply.
   The `.down.sql` file must **not** appear in the apply log.

If step 1 returns NULL (table missing because rollback actually won), restore
from backup if needed, then re-apply **only** the forward file:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f supabase/migrations/20260918200000_agentic_runtime_checkpoints.sql
```

Never re-apply the `.down.sql` file to “fix” history.
