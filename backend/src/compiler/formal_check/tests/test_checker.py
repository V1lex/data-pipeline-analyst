import json
from pathlib import Path

import pytest
from compiler.formal_check.src.models.input import Input, Variable
from compiler.formal_check.src.models.output import ModelInvalid
from compiler.formal_check.src.services import compiler, parser, validator


def model(*, reachable=False):
    return {
        "variables": [{"name": "x", "type": "integer", "min": 0, "max": 1}],
        "initial": {"x": 0},
        "transitions": [
            {"name": "Advance", "guard": {"eq": [{"var": "x"}, 0]}, "effects": [{"set": ["x", 1]}]}
        ],
        "properties": [
            {
                "id": "P",
                "type": "reachable" if reachable else "invariant",
                "expression": {"eq" if reachable else "le": [{"var": "x"}, 1]},
            }
        ],
    }


def message(body, code="2200:0"):
    return f"@!@!@STARTMSG {code} @!@!@\n{body}\n@!@!@ENDMSG {code.split(':')[0]} @!@!@\n"


PROGRESS = message(
    "Progress(1) at 2026-11-01 12:00:00: 1,234 states generated, 1,000 distinct states found"
)
SUCCESS = (
    message("Model checking completed. No error has been found.", "2193:0")
    + message("2 states generated, 2 distinct states found, 0 states left on queue.", "2199:0")
    + message("The depth of the complete state graph search is 2.", "2194:0")
)


def violation(name="P", value=1, initial=False):
    return message(
        f"Invariant {name} is violated" + (" by the initial state:" if initial else "."), "2110:1"
    ) + message(f"1: <Initial predicate>\nx = {value}", "2217:4")


@pytest.mark.parametrize(
    "stdout", [PROGRESS, PROGRESS + message("Error: Deadlock reached.", "2114:1"), ""]
)
def test_progress_and_errors_cannot_be_a_completed_verdict(stdout):
    result = json.loads(parser.parse_tlc_output(stdout, []))
    assert result["status"] == "ENGINE_ERROR"


def test_completed_output_uses_final_statistics():
    result = json.loads(parser.parse_tlc_output(PROGRESS + SUCCESS, []))
    assert result == {
        "status": "PROPERTY_HOLDS",
        "engine": "TLC",
        "statistics": {"states_generated": 2, "distinct_states": 2, "depth": 2},
    }


@pytest.mark.parametrize("initial", [False, True])
def test_counterexample_parses_single_variable_and_last_line(initial):
    result = json.loads(
        parser.parse_tlc_output(violation(initial=initial), [Variable(name="x", type="integer")])
    )
    assert result["status"] == "PROPERTY_VIOLATED"
    assert result["counterexample"]["steps"] == [
        {"number": 0, "transition": None, "state": {"x": 1}}
    ]


def test_transition_named_initial_keeps_its_name():
    stdout = message("Invariant P is violated.", "2110:1") + message(
        "1: <Initial line 5, col 1 to line 5, col 20 of module model>\nx = 1", "2217:4"
    )
    result = json.loads(parser.parse_tlc_output(stdout, [Variable(name="x", type="integer")]))
    assert result["counterexample"]["steps"][0]["transition"] == "Initial"


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(initial={}),
        lambda m: m["initial"].update(x=2),
        lambda m: m["transitions"][0]["effects"][0].update(set=["unknown", 1]),
        lambda m: m["transitions"][0]["effects"][0].update(set=["x", True]),
        lambda m: m["transitions"][0].update(guard={"eq": [{"var": "unknown"}, 0]}),
        lambda m: m["transitions"][0].update(guard={"lt": [True, 0]}),
        lambda m: m["transitions"][0].update(name="bad name"),
        lambda m: m["properties"][0].update(id="Next"),
        lambda m: m["transitions"][0]["effects"].append({"set": ["x", 0]}),
    ],
)
def test_semantically_invalid_contract_is_rejected(change):
    data = model()
    change(data)
    assert isinstance(validator.validate_input(data), ModelInvalid)


def test_compile_handles_empty_membership_and_boolean_literals(tmp_path):
    data = model()
    data["properties"] = [
        {"id": "P", "type": "invariant", "expression": {"in": [True, [True, False]]}},
        {"id": "Empty", "type": "forbidden_state", "expression": {"in": [0, []]}},
    ]
    compiler.compile_input(Input.model_validate(data), output_dir=tmp_path)
    text = (tmp_path / "model.tla").read_text()
    assert "{TRUE, FALSE}" in text
    assert "\\in {}" in text
    assert "IsDAG" not in text


def test_compile_escapes_strings_and_allows_terminal_model(tmp_path):
    value = 'quote" and slash\\ and newline\n'
    data = {
        "variables": [{"name": "label", "type": "enum", "values": [value]}],
        "initial": {"label": value},
        "transitions": [],
        "properties": [],
    }
    compiler.compile_input(Input.model_validate(data), output_dir=tmp_path / "job")
    text = (tmp_path / "job" / "model.tla").read_text()
    assert 'quote\\" and slash\\\\ and newline\\n' in text
    assert "Next == FALSE" in text


def test_check_model_aggregates_existential_reachability(monkeypatch, tmp_path):
    from compiler.formal_check.src.models.models import RunnerOutput, Status
    from compiler.formal_check.src.services import checker

    calls = []

    def run(**kwargs):
        config = Path(kwargs["config_path"])
        calls.append((config, config.read_text()))
        text = SUCCESS if len(calls) == 1 else violation("P")
        return RunnerOutput(
            stdout=text, stderr="", exit_code=0 if len(calls) == 1 else 12, status=Status.SUCCESS
        )

    monkeypatch.setattr(checker, "run_tlc", run)
    assert checker.check_model(model(reachable=True))["status"] == "PROPERTY_HOLDS"
    assert len(calls) == 2
    assert "INVARIANT P" not in calls[0][1]
    assert "INVARIANT P" in calls[1][1]
    assert not calls[0][0].exists()


def test_unreachable_target_blocks_overall_success(monkeypatch):
    from compiler.formal_check.src.models.models import RunnerOutput, Status
    from compiler.formal_check.src.services import checker

    monkeypatch.setattr(
        checker,
        "run_tlc",
        lambda **kwargs: RunnerOutput(
            stdout=SUCCESS, stderr="", exit_code=0, status=Status.SUCCESS
        ),
    )
    result = checker.check_model(model(reachable=True))
    assert result["status"] == "PROPERTY_VIOLATED"
    assert result["property_id"] == "P"


def test_check_model_timeout_and_engine_failures_never_pass(monkeypatch):
    from compiler.formal_check.src.models.models import RunnerOutput, Status
    from compiler.formal_check.src.services import checker

    monkeypatch.setattr(
        checker,
        "run_tlc",
        lambda **kwargs: RunnerOutput(
            stdout=SUCCESS, stderr="timeout", exit_code=-1, status=Status.TIMEOUT
        ),
    )
    assert checker.check_model(model())["status"] == "TIMEOUT"
    monkeypatch.setattr(
        checker,
        "run_tlc",
        lambda **kwargs: RunnerOutput(
            stdout=PROGRESS, stderr="", exit_code=0, status=Status.SUCCESS
        ),
    )
    assert checker.check_model(model())["status"] == "ENGINE_ERROR"


def test_isolated_jobs_use_different_paths(monkeypatch):
    from compiler.formal_check.src.models.models import RunnerOutput, Status
    from compiler.formal_check.src.services import checker

    paths = []

    def run(**kwargs):
        paths.append(kwargs["model_path"])
        return RunnerOutput(stdout=SUCCESS, stderr="", exit_code=0, status=Status.SUCCESS)

    monkeypatch.setattr(checker, "run_tlc", run)
    checker.check_model(model())
    checker.check_model(model())
    assert paths[0] != paths[1]


def test_real_tlc_safety_and_reachable():
    from compiler.formal_check.src.services.checker import check_model

    jar = Path("worker/tla2tools.jar")
    if not jar.is_file():
        pytest.skip("TLC jar not installed")
    assert check_model(model(), jar_path=jar)["status"] == "PROPERTY_HOLDS"
    assert check_model(model(reachable=True), jar_path=jar)["status"] == "PROPERTY_HOLDS"
    data = model()
    data["properties"][0]["expression"] = {"eq": [{"var": "x"}, 0]}
    result = check_model(data, jar_path=jar)
    assert result["status"] == "PROPERTY_VIOLATED", result
    assert result["counterexample"]["steps"][-1]["state"] == {"x": 1}
    data = model(reachable=True)
    data["properties"][0]["expression"] = {"eq": [{"var": "x"}, 2]}
    assert check_model(data, jar_path=jar)["status"] == "PROPERTY_VIOLATED"


def test_cli_reads_named_input_and_prints_normalized_result(tmp_path, monkeypatch, capsys):
    from compiler.formal_check.src import main

    source = tmp_path / "input.json"
    source.write_text(json.dumps({"variables": []}))
    monkeypatch.setattr("sys.argv", ["formal_check", str(source)])
    main.main()
    assert json.loads(capsys.readouterr().out)["status"] == "MODEL_INVALID"


def test_real_tlc_initial_violation_domains_cycles_and_strings():
    from compiler.formal_check.src.services.checker import check_model

    jar = Path("worker/tla2tools.jar")
    if not jar.is_file():
        pytest.skip("TLC jar not installed")
    data = model()
    data["properties"][0]["expression"] = {"eq": [{"var": "x"}, 1]}
    result = check_model(data, jar_path=jar)
    assert result["status"] == "PROPERTY_VIOLATED", result
    assert result["counterexample"]["steps"] == [
        {"number": 0, "transition": None, "state": {"x": 0}}
    ]
    data = model()
    data["transitions"][0].update(guard=None, effects=[{"set": ["x", {"add": [{"var": "x"}, 1]}]}])
    assert check_model(data, jar_path=jar)["status"] == "MODEL_INVALID"
    data = model()
    data["transitions"].append(
        {"name": "Return", "guard": {"eq": [{"var": "x"}, 1]}, "effects": [{"set": ["x", 0]}]}
    )
    assert check_model(data, jar_path=jar)["status"] == "PROPERTY_HOLDS"
    value = 'Кириллица " \\ \n'
    data = {
        "variables": [{"name": "label", "type": "enum", "values": [value]}],
        "initial": {"label": value},
        "transitions": [],
        "properties": [
            {"id": "P", "type": "invariant", "expression": {"ne": [{"var": "label"}, value]}}
        ],
    }
    result = check_model(data, jar_path=jar)
    assert result["status"] == "PROPERTY_VIOLATED", result
    assert result["counterexample"]["steps"][0]["state"] == {"label": value}


def test_runner_timeout_preserves_diagnostics_and_missing_jar_is_infrastructure(
    tmp_path, monkeypatch
):
    import subprocess

    from compiler.formal_check.src.models.models import Status
    from compiler.formal_check.src.services.runner import run_tlc

    data = model()
    compiler.compile_input(Input.model_validate(data), output_dir=tmp_path)
    jar = tmp_path / "tools.jar"
    jar.write_bytes(b"placeholder")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            "java", 1, output=b"partial output", stderr=b"partial error"
        )

    monkeypatch.setattr(subprocess, "run", timeout)
    output = run_tlc(
        model_path=tmp_path / "model.tla", config_path=tmp_path / "safety.cfg", jar_path=jar
    )
    assert output.status is Status.TIMEOUT
    assert output.stdout == "partial output"
    assert output.stderr == "partial error"
    assert run_tlc(jar_path=tmp_path / "missing.jar").status is Status.INFRASTRUCTURE_ERROR


def test_runner_does_not_accept_killed_process_after_witness(tmp_path, monkeypatch):
    import subprocess

    from compiler.formal_check.src.models.models import Status
    from compiler.formal_check.src.services.runner import run_tlc

    compiler.compile_input(Input.model_validate(model()), output_dir=tmp_path)
    jar = tmp_path / "tools.jar"
    jar.write_bytes(b"placeholder")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess("java", -9, violation(), ""),
    )
    output = run_tlc(
        model_path=tmp_path / "model.tla", config_path=tmp_path / "safety.cfg", jar_path=jar
    )
    assert output.status is Status.INFRASTRUCTURE_ERROR
