"""Claim and finish verification jobs without holding DB locks during TLC."""

import logging
import os
import signal
from threading import Event

from compiler.formal_check.src.services.checker import check_model
from dotenv import load_dotenv
from pipeline_generator.storage import PostgresRepository

logger = logging.getLogger(__name__)


def process_one(repository, checker=check_model) -> bool:
    run = repository.claim_verification()
    if run is None:
        return False
    try:
        specification = repository.get_specification(run["spec_id"])
        result = checker(specification["spec_json"])
    except Exception as exc:
        logger.exception("Verification job %s failed", run["id"])
        result = {
            "status": "ENGINE_ERROR",
            "engine": "TLC",
            "error": f"Verification worker failed: {type(exc).__name__}: {exc}",
        }
    repository.finish_verification(run["id"], result)
    return True


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    repository = PostgresRepository(
        os.environ["DATABASE_URL"], schema=os.getenv("DATABASE_SCHEMA", "public")
    )
    stopped = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    poll_interval = max(0.05, float(os.getenv("WORKER_POLL_INTERVAL", "1")))
    timeout = float(os.getenv("TLC_TIMEOUT", "30"))

    def check(data):
        return check_model(data, timeout=timeout)

    while not stopped.is_set():
        try:
            handled = process_one(repository, checker=check)
        except Exception:
            logger.exception("Verification queue operation failed")
            handled = False
        if not handled:
            stopped.wait(poll_interval)


if __name__ == "__main__":
    main()
