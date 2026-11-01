"""Overall verification boundary used by the persistent worker."""

import json
import math
import tempfile
import time
from pathlib import Path

from compiler.formal_check.src.models.input import PropertyType
from compiler.formal_check.src.models.models import Status
from compiler.formal_check.src.models.output import ModelInvalid
from compiler.formal_check.src.services.compiler import compile_input
from compiler.formal_check.src.services.parser import parse_tlc_output
from compiler.formal_check.src.services.runner import run_tlc
from compiler.formal_check.src.services.validator import validate_input


def check_model(data: dict, *, jar_path=None, timeout: float = 30) -> dict:
    """Check safety, then each existential reachability query within one time budget.

    Reachability is implemented by checking the invariant ``not target``. Its
    counterexample is an existential witness; exhaustion proves unreachability.
    No intermediate engine status or progress message authorizes generation.
    """
    model = validate_input(data)
    if isinstance(model, ModelInvalid):
        return model.model_dump(mode="json")
    if not isinstance(timeout, int | float) or not math.isfinite(timeout) or timeout <= 0:
        return _error("ENGINE_ERROR", "TLC timeout must be a positive finite number")
    deadline = time.monotonic() + timeout
    checked_properties = []
    diagnostics = []
    try:
        with tempfile.TemporaryDirectory(prefix="formal_check_") as tmpdir:
            directory = Path(tmpdir)
            compile_input(model, output_dir=directory)
            configurations = [(directory / "safety.cfg", None)]
            configurations.extend(
                (directory / "reachable" / f"{prop.id}.cfg", prop)
                for prop in model.properties
                if prop.type is PropertyType.REACHABLE
            )
            statistics = None
            for config_path, reachable in configurations:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return _error(
                        "TIMEOUT", "Overall TLC verification time budget exhausted", diagnostics
                    )
                output = run_tlc(
                    timeout=remaining,
                    model_path=directory / "model.tla",
                    config_path=config_path,
                    jar_path=jar_path,
                )
                diagnostics.append(
                    {
                        "configuration": config_path.name,
                        "exit_code": output.exit_code,
                        "stdout": output.stdout,
                        "stderr": output.stderr,
                    }
                )
                if output.status is not Status.SUCCESS:
                    statuses = {
                        Status.TIMEOUT: "TIMEOUT",
                        Status.INFRASTRUCTURE_ERROR: "INFRASTRUCTURE_ERROR",
                    }
                    return _error(
                        statuses.get(output.status, "ENGINE_ERROR"),
                        output.stderr or "TLC execution failed",
                        diagnostics,
                    )
                result = json.loads(parse_tlc_output(output.stdout, model.variables))
                if result["status"] == "ENGINE_ERROR":
                    return _error("ENGINE_ERROR", result["error"], diagnostics)
                if result["status"] == "PROPERTY_VIOLATED" and result["property_id"] == "TypeOK":
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
                        "counterexample": result["counterexample"],
                        "diagnostics": diagnostics,
                    }
                if reachable is None:
                    if result["status"] == "PROPERTY_VIOLATED":
                        if result["property_id"] not in {
                            p.id for p in model.properties if p.type is not PropertyType.REACHABLE
                        }:
                            return _error(
                                "ENGINE_ERROR", "TLC reported an unexpected property", diagnostics
                            )
                        return {**result, "diagnostics": diagnostics}
                    statistics = result["statistics"]
                    checked_properties.extend(
                        p.id for p in model.properties if p.type is not PropertyType.REACHABLE
                    )
                elif result["status"] == "PROPERTY_VIOLATED":
                    if result["property_id"] != reachable.id:
                        return _error(
                            "ENGINE_ERROR",
                            "Reachability run reported an unexpected property",
                            diagnostics,
                        )
                    checked_properties.append(reachable.id)
                else:
                    return {
                        "status": "PROPERTY_VIOLATED",
                        "engine": "TLC",
                        "property_id": reachable.id,
                        "counterexample": {"steps": []},
                        "statistics": result["statistics"],
                        "error": "Target state is unreachable in the completely explored state graph",
                        "diagnostics": diagnostics,
                    }
            return {
                "status": "PROPERTY_HOLDS",
                "engine": "TLC",
                "statistics": statistics,
                "checked_properties": checked_properties,
                "diagnostics": diagnostics,
            }
    except (OSError, ValueError, TypeError) as exc:
        return _error("ENGINE_ERROR", f"Verification failed: {exc}", diagnostics)


def _error(status: str, message: str, diagnostics: list | None = None) -> dict:
    return {"status": status, "engine": "TLC", "error": message, "diagnostics": diagnostics or []}
