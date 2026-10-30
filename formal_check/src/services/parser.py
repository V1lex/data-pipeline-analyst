import re

from src.models.input import Variable, VariableType
from src.models.output import (
    Counterexample,
    PropertyHolds,
    PropertyViolated,
    Statistics,
    Step,
)

type OutputResult = PropertyHolds | PropertyViolated


def parse_tlc_output(stdout: str, variables: list[Variable]) -> str:
    result = _parse(stdout, variables)
    return result.model_dump_json()


def _parse(stdout: str, variables: list[Variable]) -> OutputResult:
    message_pattern = r"@!@!@STARTMSG .*? @!@!@\n(.*?)\n@!@!@ENDMSG .*? @!@!@"
    messages = re.findall(message_pattern, stdout, re.DOTALL)

    invariant_pattern = r"Invariant (.*?) is violated\."
    violated_invariant = next(
        (m.group(1) for s in messages if (m := re.search(invariant_pattern, s))), None
    )
    if violated_invariant is not None:
        return PropertyViolated(
            property_id=violated_invariant,
            counterexample=_parse_counterexample(messages, variables),
        )

    stats_pattern = (
        r"Progress\((.*?)\) at .*?: (.*?) states generated, (.*?) distinct states found"
    )
    depth, states_generated, distinct_states = next(
        (
            int(m.group(1)),
            int(m.group(2)),
            int(m.group(3)),
        )
        for s in messages
        if (m := re.search(stats_pattern, s))
    )
    return PropertyHolds(
        statistics=Statistics(
            states_generated=states_generated,
            distinct_states=distinct_states,
            depth=depth,
        )
    )


def _parse_counterexample(
    messages: list[str], variables: list[Variable]
) -> Counterexample:
    start = next(
        i
        for i, msg in enumerate(messages)
        if msg.startswith("The behavior up to this point is:")
    )
    number = 1
    steps = []
    while messages[start + number].startswith(f"{number}: <"):
        transition_match = re.search(
            rf"{number}: <(.*?) .*?>\n", messages[start + number]
        )
        if transition_match is None:
            raise ValueError(
                f"Failed to parse transition for step {number}: {messages[start + number]}"
            )
        transition = transition_match.group(1)
        state = {}
        for i in range(len(variables)):
            var = variables[i]
            pattern = rf"/\\ {var.name} = (.*?)\n"
            value_match = re.search(pattern, messages[start + number])
            if value_match is not None:
                value_str = value_match.group(1)
                if var.type == VariableType.BOOLEAN:
                    value = value_str == "TRUE"
                elif var.type == VariableType.INTEGER:
                    value = int(value_str)
                else:
                    value = value_str.strip('"')
                state[var.name] = value
        steps.append(
            Step(
                number=number - 1,
                transition=None if transition == "Initial" else transition,
                state=state,
            )
        )
        number += 1

    return Counterexample(steps=steps)


# 1: <Initial predicate>
# /\ approved = FALSE
# /\ status = "Created"
#
# @!@!@ENDMSG 2217 @!@!@
# @!@!@STARTMSG 2217:4 @!@!@
# 2: <Approve line 10, col 12 to line 10, col 128 of module model>
# /\ approved = TRUE
# /\ status = "Approved"
