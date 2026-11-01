"""Read a canonical Input JSON file and print one normalized overall verdict."""

import argparse
import json
from pathlib import Path

from compiler.formal_check.src.services.checker import check_model


def main() -> None:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("input", type=Path)
    arguments.add_argument("--jar-path", type=Path, default=None)
    arguments.add_argument("--timeout", type=float, default=30)
    options = arguments.parse_args()
    try:
        data = json.loads(options.input.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result = {
            "status": "MODEL_INVALID",
            "engine": "TLC",
            "errors": [{"type": "INVALID_INPUT", "location": "input", "message": str(exc)}],
        }
    else:
        result = check_model(data, jar_path=options.jar_path, timeout=options.timeout)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
