"""Parse complete tool-mode TLC output, never treating progress as a verdict."""

import json
import re

from compiler.formal_check.src.models.input import Variable, VariableType
from compiler.formal_check.src.models.output import (
    Counterexample,
    EngineError,
    PropertyHolds,
    PropertyViolated,
    Statistics,
    Step,
)
from compiler.formal_check.src.services.strings import decode_string

type OutputResult = PropertyHolds | PropertyViolated | EngineError

MESSAGE = re.compile(
    r"@!@!@STARTMSG (\d+):(\d+) @!@!@\r?\n(.*?)\r?\n@!@!@ENDMSG \1 @!@!@",
    re.DOTALL,
)
VIOLATED = re.compile(
    r"Invariant ([A-Za-z_][A-Za-z_0-9]*) is violated(?:\.| by the initial state:)"
)


def parse_tlc_output(stdout: str, variables: list[Variable]) -> str:
    return _parse(stdout, variables).model_dump_json()


def _parse(stdout: str, variables: list[Variable]) -> OutputResult:
    records = MESSAGE.findall(stdout)
    messages = [body for _, _, body in records]
    violated = next((match for body in messages if (match := VIOLATED.search(body))), None)
    errors = [
        body
        for code, severity, body in records
        if severity == "1"
        and not VIOLATED.search(body)
        and not (violated is not None and code == "2121")
    ]
    if errors:
        return EngineError(error="\n".join(errors))
    if violated is not None:
        try:
            trace = _parse_counterexample(messages, variables)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            return EngineError(error=f"Invalid TLC counterexample: {exc}")
        if not trace.steps:
            return EngineError(error="TLC reported an invariant violation without a state trace")
        return PropertyViolated(property_id=violated.group(1), counterexample=trace)

    if not any("Model checking completed. No error has been found." in body for body in messages):
        return EngineError(error="TLC did not report successful model-check completion")
    # A progress sample is not the final exploration size. Parse the final summary.
    summaries = re.findall(
        r"([\d,]+) states generated, ([\d,]+) distinct states found, ([\d,]+) states left on queue\.",
        "\n".join(messages),
    )
    depths = re.findall(
        r"The depth of the complete state graph search is ([\d,]+)\.", "\n".join(messages)
    )
    if not summaries or not depths or _number(summaries[-1][2]) != 0:
        return EngineError(error="TLC completion has no exhausted state-space statistics")
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
