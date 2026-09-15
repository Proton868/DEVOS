"""Data-access layer — all authoritative application state goes through Postgres/Supabase."""
from core.repositories.agency import (
    record_work_history,
    create_mission,
    add_mission_task,
    record_delegation,
    get_mission,
    list_work_history,
    upsert_artifact_metadata,
    record_ponytail_check,
    ensure_agent,
)
