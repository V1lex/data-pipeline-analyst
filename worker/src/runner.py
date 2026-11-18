"""Claim and finish verification jobs without holding DB locks during TLC."""

import logging
import os
import signal
from threading import Event

from compiler.formal_check.src.services.checker import check_model
from dotenv import load_dotenv
from pipeline_generator.errors import PipelineError
from pipeline_generator.storage import PostgresRepository

logger = logging.getLogger(__name__)


def aggregate_verification(stage_result: dict) -> dict:
    """Convert formal-check stage envelope to the stored worker verdict."""
    validation = stage_result.get("validation") or {}
    if validation.get("status") == "MODEL_INVALID":
        return {
            "status": "MODEL_INVALID",
            "engine": "TLC",
            "errors": validation.get("errors", []),
        }

    runner = stage_result.get("runner") or {}
    outputs = runner.get("outputs", [])
    for output in outputs:
        status = output.get("status")
        if status == "timeout":
            return {
                "status": "TIMEOUT",
                "engine": "TLC",
                "timeout_limit_seconds": stage_result.get("timeout"),
                "error": output.get("stderr") or "TLC execution timed out",
            }
        if status == "state_limit_reached":
            return {
                "status": "STATE_LIMIT_REACHED",
                "engine": "TLC",
                "error": output.get("stderr") or "TLC state limit reached",
            }
        if status != "success":
            return {
                "status": "INFRASTRUCTURE_ERROR",
                "engine": "TLC",
                "error": output.get("stderr") or "TLC execution failed",
            }

    parsed = stage_result.get("parsed") or []
    checked_properties = []
    statistics = None

    for item in parsed:
        status = item.get("status")
        if status == "INFRASTRUCTURE_ERROR":
            return {
                "status": "INFRASTRUCTURE_ERROR",
                "engine": "TLC",
                "error": item.get("error", "TLC output could not be parsed"),
            }
        if status == "PROPERTY_UNREACHABLE":
            result = dict(item)
            result.pop("configuration", None)
            return result
        if item.get("configuration") != "safety.cfg" and status == "PROPERTY_VIOLATED":
            property_id = item.get("property_id")
            if property_id == "_FormalCheckTypeOK":
                return {
                    "status": "MODEL_INVALID",
                    "engine": "TLC",
                    "errors": [
                        {
                            "type": "OUT_OF_DOMAIN_VALUE",
                            "location": "transitions",
                            "message": "A reachable state violates a declared variable domain",
                        }
                    ],
                    "counterexample": item.get("counterexample"),
                }
            if property_id:
                return {
                    "status": "INFRASTRUCTURE_ERROR",
                    "engine": "TLC",
                    "error": f"Reachability run reported unexpected property {property_id}",
                }
        if item.get("configuration") == "safety.cfg" and status == "PROPERTY_VIOLATED":
            result = dict(item)
            result.pop("configuration", None)
            if result.get("property_id") == "_FormalCheckTypeOK":
                return {
                    "status": "MODEL_INVALID",
                    "engine": "TLC",
                    "errors": [
                        {
                            "type": "OUT_OF_DOMAIN_VALUE",
                            "location": "transitions",
                            "message": "A reachable state violates a declared variable domain",
                        }
                    ],
                    "counterexample": result.get("counterexample"),
                }
            return result
        if item.get("configuration") == "safety.cfg" and status == "PROPERTY_HOLDS":
            statistics = item.get("statistics")
        elif item.get("configuration", "").endswith(".cfg") and status == "PROPERTY_HOLDS":
            property_id = item.get("property_id")
            if property_id:
                checked_properties.append(property_id)
        elif item.get("configuration", "").endswith(".cfg") and status == "PROPERTY_VIOLATED":
            property_id = item.get("property_id")
            if property_id:
                checked_properties.append(property_id)

    if statistics is None:
        statistics = {}
    return {
        "status": "PROPERTY_HOLDS",
        "engine": "TLC",
        "statistics": statistics,
        "checked_properties": checked_properties,
    }


def process_one(repository, checker=check_model) -> bool:
    run = repository.claim_verification()
    if run is None:
        return False
    try:
        specification = repository.get_specification(run["spec_id"])
    except PipelineError as exc:
        if exc.code == "NOT_FOUND":
            logger.info("Discarding verification %s: its project was deleted", run["id"])
            return True
        raise
    try:
        stage_result = checker(specification["spec_json"])
        if isinstance(stage_result, dict) and {
            "validation",
            "compilation",
            "runner",
            "parsed",
        }.issubset(stage_result):
            result = aggregate_verification(stage_result)
        else:
            result = stage_result
    except Exception as exc:
        logger.exception("Verification job %s failed", run["id"])
        result = {
            "status": "INFRASTRUCTURE_ERROR",
            "engine": "TLC",
            "error": f"Verification worker failed: {type(exc).__name__}: {exc}",
        }
    try:
        repository.finish_verification(run["id"], result)
    except PipelineError as exc:
        if exc.code != "NOT_FOUND":
            raise
        logger.info("Discarding verification %s: its project was deleted", run["id"])
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
