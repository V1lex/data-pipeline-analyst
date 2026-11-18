"""Thin pipeline composition for formal-check stages."""

import math
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
