# DevOS operations (Ubuntu 24.04)

Reproducible deploy path. **Postgres/Supabase is production SoT.** No production SQLite. No PyRunner.

See product vision: `plans/DEVOS_PRODUCT_VISION.md`

## Scripts

| Script | Purpose |
|--------|---------|
| `ops/install.sh` | OS deps, venv, pip, dirs, `.env` bootstrap (no overwrite), migrations, optional systemd |
| `ops/update.sh` | ff-only git → deps → migrations → systemd → restart → verify |
| `ops/verify.sh` | env, imports, DB, service, `/api/health` (secrets not printed) |
| `ops/apply_migrations.sh` | `scripts/migrate_agency_schema.py` (+ optional resume) |
| `ops/install_systemd.sh` | Render `devos.service` from template |
| `ops/env_validate.py` | Production/dev env rules |

## Fresh VPS

```bash
git clone https://github.com/Proton868/DEVOS.git ~/devos
cd ~/devos
DEVOS_DEPLOY_MODE=development ./ops/install.sh
# edit .env: DATABASE_URL (Supabase), JWT_SECRET, ADMIN_PASSWORD, DEBUG=false
DEVOS_DEPLOY_MODE=production DEVOS_INSTALL_SYSTEMD=1 ./ops/install.sh
sudo systemctl start devos
./ops/verify.sh --production
```

## Update

```bash
cd ~/devos
DEVOS_DEPLOY_MODE=production ./ops/update.sh
```

## Rollback

- App: check out previous tag/commit and run `ops/update.sh` (or restart prior unit).
- DB: ops never runs destructive down-migrations; restore from backup if required.

## Frontend

`ops/` targets API/service. For SPA assets run root `./install.sh` once (Node build) or CI.
