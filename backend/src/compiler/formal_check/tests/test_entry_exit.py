"""Entry-to-output module tests for the formal-check public pipeline."""

from pathlib import Path

import pytest
from compiler.formal_check.src.models.models import RunnerOutput, Status
from compiler.formal_check.src.services import checker


def message(body: str, code: str = "2200:0") -> str:
    return f"@!@!@STARTMSG {code} @!@!@\n{body}\n@!@!@ENDMSG {code.split(':')[0]} @!@!@\n"


SUCCESS = (
    message("Model checking completed. No error has been found.", "2193:0")
    + message("2 states generated, 2 distinct states found, 0 states left on queue.", "2199:0")
    + message("The depth of the complete state graph search is 2.", "2194:0")
)


def violation(property_id: str = "P", value: int = 1) -> str:
    return message(
        f"Invariant {property_id} is violated.",
        "2110:1",
    ) + message(
        f"1: <Initial predicate>\nx = {value}",
        "2217:4",
    )


def base_model(*, reachable: bool = False, deadlock_free: bool = False) -> dict:
    properties = []
    if deadlock_free:
        properties.append(
            {
                "id": "PDeadlock",
                "type": "deadlock_free",
                "expression": {"eq": [{"var": "x"}, 1]},
            }
        )
    properties.append(
        {
            "id": "P",
            "type": "reachable" if reachable else "invariant",
            "expression": {"eq": [{"var": "x"}, 1]} if reachable else {"le": [{"var": "x"}, 1]},
        }
    )
    return {
        "variables": [{"name": "x", "type": "integer", "min": 0, "max": 1}],
        "initial": {"x": 0},
        "transitions": [
            {
                "name": "Advance",
                "guard": {"eq": [{"var": "x"}, 0]},
                "effects": [{"set": ["x", 1]}],
            }
        ],
        "properties": properties,
    }


def test_raw_pydantic_invalid_input_stops_before_compilation() -> None:
    result = checker.check_model({"variables": []})

    assert result["validation"]["status"] == "MODEL_INVALID"
    assert result["compilation"] is None
    assert result["runner"] is None
    assert result["parsed"] == []


def test_raw_semantic_invalid_input_stops_before_runner() -> None:
    data = base_model()
    data["initial"] = {"unknown": 0}

    result = checker.check_model(data)

    assert result["validation"]["status"] == "MODEL_INVALID"
    assert result["compilation"] is None
    assert result["runner"] is None
    assert result["parsed"] == []
    assert any(
        error["type"] == "UNDEFINED_VARIABLE_REFERENCE" for error in result["validation"]["errors"]
    )


def test_invalid_timeout_stops_before_compilation() -> None:
    result = checker.check_model(base_model(), timeout=0)

    assert "variables" in result["validation"]
    assert result["compilation"] is None
    assert result["runner"]["status"] == "invalid_timeout"
    assert result["parsed"] == []


def test_safety_success_reaches_all_output_stages(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        checker,
        "run_tlc",
        lambda **kwargs: RunnerOutput(
            stdout=SUCCESS,
            stderr="",
            exit_code=0,
            status=Status.SUCCESS,
        ),
    )

    result = checker.check_model(base_model(), output_dir=tmp_path)

    assert "variables" in result["validation"]
    assert result["compilation"]["model_path"].endswith("model.tla")
    assert result["runner"]["outputs"][0]["status"] == "success"
    assert result["parsed"][0]["status"] == "PROPERTY_HOLDS"
    assert result["parsed"][0]["statistics"]["distinct_states"] == 2


def test_safety_violation_is_returned_with_counterexample(monkeypatch) -> None:
    monkeypatch.setattr(
        checker,
        "run_tlc",
        lambda **kwargs: RunnerOutput(
            stdout=violation("P"),
            stderr="",
            exit_code=12,
            status=Status.SUCCESS,
        ),
    )

    result = checker.check_model(base_model())

    assert result["parsed"][0]["status"] == "PROPERTY_VIOLATED"
    assert result["parsed"][0]["property_id"] == "P"
    assert result["parsed"][0]["counterexample"]["steps"][0]["state"] == {"x": 1}


def test_internal_type_violation_is_distinguishable(monkeypatch) -> None:
    monkeypatch.setattr(
        checker,
        "run_tlc",
        lambda **kwargs: RunnerOutput(
            stdout=violation("_FormalCheckTypeOK", value=2),
            stderr="",
            exit_code=12,
            status=Status.SUCCESS,
        ),
    )

    result = checker.check_model(base_model())

    assert result["parsed"][0]["status"] == "PROPERTY_VIOLATED"
    assert result["parsed"][0]["property_id"] == "_FormalCheckTypeOK"


def test_reach_typeok_violation_is_not_successful_reachability(monkeypatch) -> None:
    def run(**kwargs):
        config = Path(kwargs["config_path"])
        stdout = (
            SUCCESS if config.name == "safety.cfg" else violation("_FormalCheckTypeOK", value=2)
        )
        return RunnerOutput(
            stdout=stdout,
            stderr="",
            exit_code=0 if config.name == "safety.cfg" else 12,
            status=Status.SUCCESS,
        )

    monkeypatch.setattr(checker, "run_tlc", run)
    result = checker.check_model(base_model(reachable=True))
    reachable = result["parsed"][1]

    assert reachable["status"] == "PROPERTY_VIOLATED"
    assert reachable["property_id"] == "_FormalCheckTypeOK"


def test_reach_timeout_is_not_unreachable(monkeypatch) -> None:
    monkeypatch.setattr(
        checker,
        "run_tlc",
        lambda **kwargs: RunnerOutput(
            stdout="",
            stderr="TLC execution timed out",
            exit_code=-1,
            status=Status.TIMEOUT,
        ),
    )

    result = checker.check_model(base_model(reachable=True))

    assert result["runner"]["outputs"]
    assert all(output["status"] == "timeout" for output in result["runner"]["outputs"])
    assert not any(item.get("status") == "PROPERTY_UNREACHABLE" for item in result["parsed"])


def test_reach_infrastructure_error_is_not_unreachable(monkeypatch) -> None:
    monkeypatch.setattr(
        checker,
        "run_tlc",
        lambda **kwargs: RunnerOutput(
            stdout="",
            stderr="Java process exited with code 137",
            exit_code=137,
            status=Status.INFRASTRUCTURE_ERROR,
        ),
    )

    result = checker.check_model(base_model(reachable=True))

    assert result["runner"]["outputs"]
    assert all(output["status"] == "infrastructure_error" for output in result["runner"]["outputs"])
    assert not any(item.get("status") == "PROPERTY_UNREACHABLE" for item in result["parsed"])


def test_reachable_witness_and_unreachable_exhaustion(monkeypatch) -> None:
    def witness(**kwargs):
        config = Path(kwargs["config_path"])
        stdout = SUCCESS if config.name == "safety.cfg" else violation("P")
        return RunnerOutput(
            stdout=stdout,
            stderr="",
            exit_code=0 if config.name == "safety.cfg" else 12,
            status=Status.SUCCESS,
        )

    monkeypatch.setattr(checker, "run_tlc", witness)
    result = checker.check_model(base_model(reachable=True))
    assert result["parsed"][0]["status"] == "PROPERTY_HOLDS"
    assert result["parsed"][1]["status"] == "PROPERTY_HOLDS"
    assert result["parsed"][1]["property_id"] == "P"

    monkeypatch.setattr(
        checker,
        "run_tlc",
        lambda **kwargs: RunnerOutput(
            stdout=SUCCESS,
            stderr="",
            exit_code=0,
            status=Status.SUCCESS,
        ),
    )
    result = checker.check_model(base_model(reachable=True))
    assert result["parsed"][0]["status"] == "PROPERTY_HOLDS"
    assert result["parsed"][1]["status"] == "PROPERTY_UNREACHABLE"
    assert result["parsed"][1]["property_id"] == "P"
    assert "counterexample" not in result["parsed"][1]


def test_multiple_reach_properties_are_interpreted_by_compilation_mapping(monkeypatch) -> None:
    data = base_model(reachable=True)
    data["properties"].append(
        {
            "id": "P2",
            "type": "reachable",
            "expression": {"eq": [{"var": "x"}, 1]},
        }
    )

    def run(**kwargs):
        config = Path(kwargs["config_path"])
        if config.name == "safety.cfg":
            stdout = SUCCESS
        elif config.name == "P.cfg":
            stdout = violation("P")
        else:
            stdout = SUCCESS
        return RunnerOutput(
            stdout=stdout,
            stderr="",
            exit_code=12 if config.name == "P.cfg" else 0,
            status=Status.SUCCESS,
        )

    monkeypatch.setattr(checker, "run_tlc", run)
    result = checker.check_model(data)

    reachable = next(item for item in result["parsed"] if item["configuration"] == "P.cfg")
    unreachable = next(item for item in result["parsed"] if item["configuration"] == "P2.cfg")
    assert reachable["status"] == "PROPERTY_HOLDS"
    assert reachable["property_id"] == "P"
    assert unreachable["status"] == "PROPERTY_UNREACHABLE"
    assert unreachable["property_id"] == "P2"
    assert "counterexample" not in unreachable


def test_mixed_reachable_and_invariant_batch(monkeypatch) -> None:
    data = base_model(reachable=True)
    data["properties"].insert(
        0,
        {
            "id": "Safe",
            "type": "invariant",
            "expression": {"le": [{"var": "x"}, 1]},
        },
    )

    def run(**kwargs):
        config = Path(kwargs["config_path"])
        if config.name == "P.cfg":
            stdout = violation("P")
            exit_code = 12
        else:
            stdout = SUCCESS
            exit_code = 0
        return RunnerOutput(
            stdout=stdout,
            stderr="",
            exit_code=exit_code,
            status=Status.SUCCESS,
        )

    monkeypatch.setattr(checker, "run_tlc", run)
    result = checker.check_model(data)

    assert result["parsed"][0]["status"] == "PROPERTY_HOLDS"
    assert result["parsed"][1]["status"] == "PROPERTY_HOLDS"
    assert result["parsed"][1]["property_id"] == "P"


def test_deadlock_property_is_compiled_and_run(monkeypatch) -> None:
    def run(**kwargs):
        config = Path(kwargs["config_path"])
        text = config.read_text()
        assert "INVARIANT PDeadlock" in text
        return RunnerOutput(
            stdout=SUCCESS,
            stderr="",
            exit_code=0,
            status=Status.SUCCESS,
        )

    monkeypatch.setattr(checker, "run_tlc", run)
    result = checker.check_model(base_model(deadlock_free=True))

    assert result["parsed"][0]["status"] == "PROPERTY_HOLDS"


@pytest.mark.parametrize(
    ("status", "stderr", "exit_code"),
    [
        (Status.TIMEOUT, "TLC execution timed out", -1),
        (Status.INFRASTRUCTURE_ERROR, "Java process exited with code 137", 137),
        (Status.INFRASTRUCTURE_ERROR, "TLC engine failed", 1),
    ],
)
def test_runner_failures_are_preserved(monkeypatch, status, stderr, exit_code) -> None:
    monkeypatch.setattr(
        checker,
        "run_tlc",
        lambda **kwargs: RunnerOutput(
            stdout="",
            stderr=stderr,
            exit_code=exit_code,
            status=status,
        ),
    )

    result = checker.check_model(base_model())

    assert result["runner"]["outputs"][0]["status"] == status.value
    assert result["runner"]["outputs"][0]["stderr"] == stderr
    assert result["parsed"] == []


def test_progress_only_is_infrastructure_error(monkeypatch) -> None:
    progress = message(
        "Progress(1) at 2026-11-01 12:00:00: 1,234 states generated, 1,000 distinct states found"
    )
    monkeypatch.setattr(
        checker,
        "run_tlc",
        lambda **kwargs: RunnerOutput(
            stdout=progress,
            stderr="",
            exit_code=0,
            status=Status.SUCCESS,
        ),
    )

    result = checker.check_model(base_model())

    assert result["parsed"][0]["status"] == "INFRASTRUCTURE_ERROR"
