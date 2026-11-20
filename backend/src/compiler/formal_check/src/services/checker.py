"""Thin pipeline composition for formal-check stages."""

import math
import os
import tempfile
from pathlib import Path

from compiler.formal_check.src.models.output import ModelInvalid
from compiler.formal_check.src.services.compiler import compile_input
from compiler.formal_check.src.services.parser import parse_tlc_batch
from compiler.formal_check.src.services.runner import run_tlc, run_tlc_batch
from compiler.formal_check.src.services.validator import validate_input


def check_model(
    data: dict,
    *,
    jar_path=None,
    timeout: float = 30,
    output_dir: Path | str | None = None,
) -> dict:
    """Compose validator -> compiler -> runner -> parser as a transparent pipeline."""
    model = validate_input(data)
    if isinstance(model, ModelInvalid):
        return {
            "validation": model.model_dump(mode="json"),
            "compilation": None,
            "runner": None,
            "parsed": [],
        }
    if not isinstance(timeout, int | float) or not math.isfinite(timeout) or timeout <= 0:
        return {
            "validation": model.model_dump(mode="json"),
            "compilation": None,
            "runner": {
                "status": "invalid_timeout",
                "error": "TLC timeout must be a positive finite number",
            },
            "parsed": [],
        }

    # Conservative admission control: bound the complete declared state space,
    # rather than assuming that a large domain happens to have few reachable states.
    try:
        state_limit = int(os.getenv("TLC_MAX_STATES", "1000000"))
        if state_limit < 1:
            raise ValueError
    except ValueError:
        return {
            "validation": model.model_dump(mode="json"),
            "compilation": None,
            "runner": {
                "status": "invalid_state_limit",
                "error": "TLC_MAX_STATES must be a positive integer",
            },
            "parsed": [],
        }
    possible_states = 1
    for variable in model.variables:
        if variable.type == "boolean":
            size = 2
        elif variable.type == "enum":
            size = len(variable.values)
        else:
            size = variable.max - variable.min + 1
        possible_states *= size
        if possible_states > state_limit:
            return {
                "validation": model.model_dump(mode="json"),
                "compilation": None,
                "runner": {
                    "outputs": [
                        {
                            "status": "state_limit_reached",
                            "stderr": f"Declared state space has at least {possible_states} states; limit is {state_limit}. This conservative bound can exceed the number of reachable states.",
                        }
                    ]
                },
                "parsed": [],
            }

    def run(directory: Path) -> dict:
        compilation = compile_input(model, output_dir=directory)
        configs = [compilation.safety_config_path]
        configs.extend(compilation.reachable_config_paths.values())
        batch = run_tlc_batch(
            compilation.model_path,
            configs,
            timeout=timeout,
            jar_path=jar_path,
            run_tlc_fn=run_tlc,
        )
        parsed = parse_tlc_batch(
            config_paths=configs,
            runner_outputs=batch.outputs,
            variables=model.variables,
            reachable_config_paths=compilation.reachable_config_paths,
        )
        return {
            "validation": model.model_dump(mode="json"),
            "compilation": compilation.model_dump(mode="json"),
            "runner": batch.model_dump(mode="json"),
            "parsed": parsed,
        }

    if output_dir is not None:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return run(directory)

    with tempfile.TemporaryDirectory(prefix="formal_check_") as tmpdir:
        return run(Path(tmpdir))
