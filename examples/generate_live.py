"""Explicit development-only Gemini call with a SYNTHETIC verification fixture; never executes code."""

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from pipeline_generator.errors import PipelineError
from pipeline_generator.gemini import GeminiClient
from pipeline_generator.generator import PySparkGenerator
from pipeline_generator.models import VerifiedContext


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--acknowledge-synthetic-verification",
        action="store_true",
        required=True,
        help="The fixture is not evidence of a real TLC check",
    )
    args = parser.parse_args()
    client = None
    try:
        context = VerifiedContext.model_validate_json(args.context.read_text("utf-8"))
        client = GeminiClient.from_environment(env_file=args.env_file)
        result = PySparkGenerator(client).generate(context)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        name = f"{Path(result.filename).stem}-{uuid4().hex}.py"
        target = args.output_dir / name
        with target.open("x", encoding="utf-8") as output:
            output.write(result.content)
        metadata = {
            **result.model_dump(mode="json"),
            "filename": name,
            "context_sha256": context.fingerprint(),
            "verification_fixture": True,
            "executed": False,
        }
        with target.with_suffix(".provenance.json").open("x", encoding="utf-8") as output:
            json.dump(metadata, output, ensure_ascii=False, indent=2)
        print(
            json.dumps(
                {
                    "status": "GENERATED",
                    "answer": result.answer,
                    "filename": str(target),
                    "verification_fixture": True,
                    "executed": False,
                }
            )
        )
        return 0
    except PipelineError as error:
        print(json.dumps(error.to_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
