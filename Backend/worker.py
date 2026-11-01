import sys
from pathlib import Path

# pythonw.exe (used by the Windows scheduled tasks) has no console, so
# sys.stdout/sys.stderr are None and any print crashes. Log to a file instead.
if sys.stdout is None or sys.stderr is None:
    _log_dir = Path(__file__).resolve().parent.parent / "logs"
    _log_dir.mkdir(exist_ok=True)
    _log_file = open(_log_dir / "worker.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log_file
    sys.stderr = sys.stderr or _log_file

# Child processes (ffmpeg) started from pythonw.exe would each open a console
# window; closing one kills the render (exit 0xC000013A). Start them hidden.
import os as _os
if _os.name == "nt":
    import subprocess as _subprocess

    _original_popen_init = _subprocess.Popen.__init__

    def _popen_no_window(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        _original_popen_init(self, *args, **kwargs)

    _subprocess.Popen.__init__ = _popen_no_window


import time

from dotenv import load_dotenv

from db import SessionLocal, init_db
from pipeline import PipelineCancelled, run_generation_pipeline
from repository import (
    append_event,
    claim_next_queued_job,
    get_job,
    mark_cancelled,
    mark_completed,
    mark_failed,
)
from utils import ENV_FILE, SUBTITLES_DIR, TEMP_DIR, check_env_vars, clean_dir


POLL_SECONDS = 1.0


def _job_cancelled(job_id: str) -> bool:
    with SessionLocal() as session:
        job = get_job(session, job_id)
        if not job:
            return True
        return bool(job.cancel_requested or job.status == "cancelled")


def _log_event(job_id: str, message: str, level: str) -> None:
    with SessionLocal() as session:
        append_event(session, job_id, "log", level, str(message))
        session.commit()


def process_next_job() -> bool:
    with SessionLocal() as session:
        job = claim_next_queued_job(session)

    if not job:
        return False

    job_id = job.id

    clean_dir(str(TEMP_DIR))
    clean_dir(str(SUBTITLES_DIR))

    try:
        result_path = run_generation_pipeline(
            data=job.payload,
            is_cancelled=lambda: _job_cancelled(job_id),
            on_log=lambda message, level: _log_event(job_id, message, level),
        )
        with SessionLocal() as session:
            mark_completed(session, job_id, result_path)
    except PipelineCancelled as err:
        with SessionLocal() as session:
            mark_cancelled(session, job_id, str(err))
    except Exception as err:
        with SessionLocal() as session:
            mark_failed(session, job_id, str(err))

    return True


def main() -> None:
    load_dotenv(ENV_FILE)
    check_env_vars()
    init_db()

    while True:
        processed = process_next_job()
        if not processed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
