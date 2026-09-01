"""APScheduler — Brain can schedule scripts to run automatically"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger("devos.scheduler")
_scheduler = None

async def start_scheduler():
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.start()
    await _reload_jobs()
    logger.info("✅ Scheduler running")

def stop_scheduler():
    """Graceful shutdown hook (security-audit P4d), called from app.py's
    lifespan on server shutdown. wait=False so shutdown doesn't hang waiting
    for a scheduled script that happens to be mid-run -- the run itself
    isn't cancelled, just the scheduler's own event loop."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("🛑 Scheduler stopped")

async def _reload_jobs():
    from core.database import AsyncSessionLocal, Script
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Script).where(Script.is_active==True, Script.schedule_type.in_(["cron","interval"])))
        for s in r.scalars().all():
            _schedule_script(s)

def _schedule_script(script):
    if not _scheduler: return
    jid = f"script_{script.id}"
    if _scheduler.get_job(jid): _scheduler.remove_job(jid)
    try:
        if script.schedule_type == "cron":
            trigger = CronTrigger.from_crontab(script.schedule_value or "0 * * * *")
        else:
            trigger = IntervalTrigger(seconds=int(script.schedule_value or 3600))
        _scheduler.add_job(_run_script, trigger, id=jid, args=[script.id], name=script.name, replace_existing=True)
    except Exception as e:
        logger.warning(f"Schedule failed for {script.name}: {e}")

def schedule_script(script):
    """Public entry point — called by api/routes/scripts.py's create/update
    handlers so a schedule change takes effect immediately, not only on the
    next server restart (the real gap found in record.md Session 22:
    _reload_jobs() only ever ran once, at startup)."""
    _schedule_script(script)

def unschedule_script(script_id: str):
    """Public entry point for delete/deactivate — removes a live job if one
    exists. Safe to call even if the script was never scheduled."""
    if not _scheduler:
        return
    jid = f"script_{script_id}"
    if _scheduler.get_job(jid):
        _scheduler.remove_job(jid)

async def _run_script(script_id: str):
    # Routed through execution/script_runner.py's run_and_record() (G9) so
    # scheduled runs get the same retry policy, success/failure
    # notifications, and script-chaining as manual and webhook triggers --
    # previously this had its own separate copy of the run/record logic
    # with none of that behavior.
    from execution.script_runner import run_and_record
    await run_and_record(script_id, trigger="scheduled")
