import logging.config
from pathlib import Path

import yaml

from .services import runner

config_path = Path(__file__).resolve().parents[1] / "log-config.yaml"


def main() -> None:
    output = runner.run_tlc()
    print(output.stdout, output.stderr, output.exit_code)


if __name__ == "__main__":
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f.read())
    logging.config.dictConfig(config)
    main()
