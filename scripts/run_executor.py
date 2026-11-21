"""Run the trusted execution queue consumer on the host without exposing Docker socket to an app container."""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

ALLOWED = {"DATABASE_URL", "DATABASE_SCHEMA", "EXECUTION_IMAGE", "EXECUTION_TIMEOUT", "LOG_LEVEL"}


def main():
    project_root = str(Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    allowed_environment = ALLOWED | {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
        "XDG_RUNTIME_DIR",
    }
    for name in list(os.environ):
        if name not in allowed_environment:
            os.environ.pop(name, None)
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.exists():
        # Copy only executor settings into the process environment.
        import io

        with env_file.open(encoding="utf-8") as source:
            selected = "\n".join(
                line for line in source if line.partition("=")[0].strip() in ALLOWED
            )
        for name, value in dotenv_values(stream=io.StringIO(selected)).items():
            if name in ALLOWED and value is not None:
                os.environ.setdefault(name, value)
    from execution.worker import main as run

    run()


if __name__ == "__main__":
    main()
