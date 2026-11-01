import json
import logging.config
from pathlib import Path

import yaml

from src.models.output import ModelInvalid
from src.services import compiler, parser, runner
from src.services.validator import validate_input

config_path = Path(__file__).resolve().parents[1] / "log-config.yaml"


def main() -> None:
    for file_name in ["src/test.json", "src/test_property_violated.json"]:
        with open(file_name, encoding="utf-8") as f:
            raw_input = json.load(f)
        input_data = validate_input(raw_input)
        if isinstance(input_data, ModelInvalid):
            logging.error("Wrong input data. Exiting.")
            logging.error(input_data.errors)
            return

        compiler.compile_input(input_data)
        result = runner.run_tlc(timeout=3.0)

        print(parser.parse_tlc_output(result.stdout, input_data.variables))


if __name__ == "__main__":
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f.read())
    logging.config.dictConfig(config)
    main()
