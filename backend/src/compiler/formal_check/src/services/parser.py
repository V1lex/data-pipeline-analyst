"""Parse complete tool-mode TLC output, never treating progress as a verdict."""

import json
import re
from pathlib import Path

from compiler.formal_check.src.models.input import Variable, VariableType
from compiler.formal_check.src.models.models import RunnerOutput, Status
from compiler.formal_check.src.models.output import (
    Counterexample,
    InfrastructureError,
    PropertyHolds,
    PropertyUnreachable,
    PropertyViolated,
    Statistics,
    Step,
)
from compiler.formal_check.src.services.strings import decode_string

type OutputResult = PropertyHolds | PropertyViolated | InfrastructureError

MESSAGE = re.compile(
    r"@!@!@STARTMSG (\d+):(\d+) @!@!@\r?\n(.*?)\r?\n@!@!@ENDMSG \1 @!@!@",
    re.DOTALL,
)
VIOLATED = re.compile(
    r"Invariant ([A-Za-z_][A-Za-z_0-9]*) is violated(?:\.| by the initial state:)"
)
TEMPORAL_VIOLATED = "Temporal properties were violated."


def parse_tlc_output(
    stdout: str,
    variables: list[Variable],
) -> OutputResult:
    return _parse(stdout, variables)


def parse_tlc_batch(
    config_paths: list[Path],
    runner_outputs: list[RunnerOutput],
    variables: list[Variable],
    reachable_config_paths: dict[str, Path],
) -> list[dict]:
    """Parse a batch and apply REACHABLE semantics using compilation metadata."""
    reverse_reachable = {
        str(path.resolve(strict=False)): property_id
        for property_id, path in reachable_config_paths.items()
    }
    parsed_items = []
    for config_path, runner_output in zip(config_paths, runner_outputs, strict=True):
        if runner_output.status is not Status.SUCCESS:
            continue

        parsed = parse_tlc_output(runner_output.stdout, variables)
        reachable_property_id = reverse_reachable.get(str(config_path.resolve(strict=False)))
        if reachable_property_id is not None:
            if isinstance(parsed, PropertyViolated):
                if parsed.property_id == "_FormalCheckTypeOK":
                    parsed_item = parsed.model_dump(mode="json")
                elif parsed.property_id == reachable_property_id:
                    parsed_item = {
                        "status": "PROPERTY_HOLDS",
                        "engine": "TLC",
                        "property_id": reachable_property_id,
                    }
                else:
                    parsed_item = InfrastructureError(
                        error=(
                            f"Reachability run reported unexpected property {parsed.property_id}"
                        )
                    ).model_dump(mode="json")
            elif isinstance(parsed, PropertyHolds):
                parsed_item = PropertyUnreachable(
                    property_id=reachable_property_id,
                    error=("Target state is unreachable in the completely explored state graph"),
                ).model_dump(mode="json")
            else:
                parsed_item = parsed.model_dump(mode="json")
        else:
            parsed_item = parsed.model_dump(mode="json")
        parsed_items.append({"configuration": config_path.name, **parsed_item})
    return parsed_items


def _parse(
    stdout: str,
    variables: list[Variable],
) -> OutputResult:
    records = MESSAGE.findall(stdout)
    messages = [body for _, _, body in records]
    violated = next((match for body in messages if (match := VIOLATED.search(body))), None)
    temporal_violated = any(TEMPORAL_VIOLATED in body for body in messages)
    errors = [
        body
        for code, severity, body in records
        if severity == "1"
        and not temporal_violated
        and not VIOLATED.search(body)
        and TEMPORAL_VIOLATED not in body
        and not (violated is not None and code == "2121")
    ]
    if errors:
        return InfrastructureError(error="\n".join(errors))
    if violated is not None:
        try:
            trace = _parse_counterexample(messages, variables)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            return InfrastructureError(error=f"Invalid TLC counterexample: {exc}")
        if not trace.steps:
            return InfrastructureError(
                error="TLC reported an invariant violation without a state trace"
            )
        return PropertyViolated(property_id=violated.group(1), counterexample=trace)
    if temporal_violated:
        try:
            trace = _parse_counterexample(messages, variables)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            return InfrastructureError(error=f"Invalid TLC temporal counterexample: {exc}")
        if not trace.steps:
            return InfrastructureError(
                error="TLC reported a temporal violation without a state trace"
            )
        return PropertyViolated(property_id="IsDAG", counterexample=trace)

    if not any("Model checking completed. No error has been found." in body for body in messages):
        return InfrastructureError(error="TLC did not report successful model-check completion")
    # A progress sample is not the final exploration size. Parse the final summary.
    summaries = re.findall(
        r"([\d,]+) states generated, ([\d,]+) distinct states found, ([\d,]+) states left on queue\.",
        "\n".join(messages),
    )
    depths = re.findall(
        r"The depth of the complete state graph search is ([\d,]+)\.", "\n".join(messages)
    )
    if not summaries or not depths or _number(summaries[-1][2]) != 0:
        return InfrastructureError(error="TLC completion has no exhausted state-space statistics")
    generated, distinct, _ = summaries[-1]
    return PropertyHolds(
        statistics=Statistics(
            states_generated=_number(generated),
            distinct_states=_number(distinct),
            depth=_number(depths[-1]),
        )
    )


def _number(value: str) -> int:
    return int(value.replace(",", ""))


def _parse_counterexample(messages: list[str], variables: list[Variable]) -> Counterexample:
    steps = []
    for body in messages:
        initial = re.match(r"Invariant .* is violated by the initial state:\n", body)
        if initial is not None:
            body = "1: <Initial predicate>\n" + body[initial.end() :]
        header = re.match(r"(?:State )?(\d+): <([^\n]+)>\n", body)
        if header is None:
            continue
        label = header.group(2)
        state = {}
        for variable in variables:
            match = re.search(
                rf"^(?:/\\\s+)?{re.escape(variable.name)}\s*=\s*(.*?)\s*$", body, re.MULTILINE
            )
            if match is None:
                raise ValueError(f"Missing variable {variable.name} in state {header.group(1)}")
            raw = match.group(1)
            if variable.type == VariableType.INTEGER:
                value = int(raw)
            elif variable.type == VariableType.BOOLEAN:
                if raw not in {"TRUE", "FALSE"}:
                    raise ValueError(f"Invalid Boolean {raw}")
                value = raw == "TRUE"
            else:
                value = json.loads(raw)
                if not isinstance(value, str):
                    raise ValueError(f"Invalid string {raw}")
                value = decode_string(value)
            state[variable.name] = value
        transition = None if label == "Initial predicate" else label.split(" line ", 1)[0]
        steps.append(Step(number=int(header.group(1)) - 1, transition=transition, state=state))
    return Counterexample(steps=steps)
