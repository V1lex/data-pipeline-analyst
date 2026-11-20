"""Opt-in Docker tests with saved, previously generated Polza artifacts and real TLC."""

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from pipeline_generator.generator import PySparkGenerator
from pipeline_generator.models import Completion
from pipeline_generator.service import GenerationService

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.docker,
    pytest.mark.skipif(
        os.getenv("RUN_DOCKER_TESTS") != "1",
        reason="Set RUN_DOCKER_TESTS=1 and build dpa-spark-runner:local",
    ),
]


@pytest.mark.parametrize(
    "name, arguments, expected",
    [
        (
            "revenue",
            {"--orders": "orders.csv", "--prices": "prices.csv"},
            {"Казань": 150, "Москва": 300},
        ),
        ("sensor", {"--readings": "readings.csv"}, {"north": (15.0, 2), "south": (-5.0, 1)}),
    ],
)
def test_saved_polza_code_executes_isolated_and_produces_known_results(
    repository, name, arguments, expected
):
    from execution.storage import ExecutionStore
    from execution.worker import process_one

    from worker.src.runner import process_one as verify_one

    fixture = json.loads(Path(f"examples/{name}_context.json").read_text())
    saved_code = next(Path(f"examples/live-results/{name}").glob("*.py")).read_text()
    project = repository.create_project("Synthetic " + name)
    ids = {}
    for flag, filename in arguments.items():
        content = Path("examples/data", filename).read_bytes()
        ids[flag] = repository.store_source_file(
            project, filename, content, {"format": "csv", "columns": []}, max_files=3
        )
    repository.select_source_files(project, list(ids.values()))
    spec = repository.create_specification(
        project,
        fixture["behavioral_model"],
        fixture["approved_requirements"],
        [],
        fixture["target_environment"],
    )
    repository.approve_specification(spec)
    repository.enqueue_verification(spec)
    assert verify_one(repository)

    class ReissuedSavedCode:
        def complete(self, system, user, schema):
            payload = {
                "status": "GENERATED",
                "answer": "Сохранённый файл для теста.",
                "filename": name + ".py",
                "content": saved_code,
                "requirements": ["pyspark==3.5.8"],
                "notes": [],
                "questions": [],
            }
            return Completion(
                text=json.dumps(payload),
                finish_reason="STOP",
                provider="saved-test-artifact",
                model="deepseek/deepseek-v4.1-flash-1017",
            )

    artifact = GenerationService(repository, PySparkGenerator(ReissuedSavedCode())).generate(
        spec, uuid4()
    )
    store = ExecutionStore(repository)
    run = store.enqueue(spec, artifact.artifact_id, ids, uuid4())
    assert process_one(repository)
    result = store.get(spec, run, include_archive=True)
    assert result["status"] == "SUCCEEDED", result.get("error")
    assert result["result_csv"] and result["result_archive"]
    rows = result["result_preview"]["rows"]
    if name == "revenue":
        assert {row["city"]: int(float(row["revenue"])) for row in rows} == expected
    else:
        assert {
            row["sensor"]: (row["mean_temperature"], row["reading_count"]) for row in rows
        } == expected


def test_sandbox_blocks_network_secrets_and_input_writes():
    from executor.docker_runner import run_isolated

    source = uuid4()
    code = """import argparse, os, socket
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--output'); a=p.parse_args()
assert 'POLZA_API_KEY' not in os.environ and 'DATABASE_URL' not in os.environ
try:
 open(a.input, 'w')
 raise RuntimeError('input was writable')
except PermissionError: pass
s=socket.socket(); s.settimeout(1)
try:
 s.connect(('1.1.1.1',80))
 raise RuntimeError('network was available')
except OSError: pass
Path(a.output).mkdir(); (Path(a.output)/'result.csv').write_text('ok,value\\nyes,1\\n')
"""
    result = run_isolated(
        code, {source: {"format": "csv", "content": b"a,b\n1,2\n"}}, {"--input": source}, timeout=20
    )
    assert result["status"] == "SUCCEEDED", result


def test_sandbox_terminates_overdue_process():
    from executor.docker_runner import run_isolated

    source = uuid4()
    code = "import time; time.sleep(20)"
    result = run_isolated(
        code, {source: {"format": "csv", "content": b"a,b\n1,2\n"}}, {"--input": source}, timeout=1
    )
    assert result["status"] == "TIMEOUT"


def test_sandbox_passes_explicit_parameters():
    import io
    import zipfile

    from executor.docker_runner import run_isolated

    source = uuid4()
    code = """import argparse
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--output'); p.add_argument('--threshold'); a=p.parse_args()
Path(a.output).mkdir(); (Path(a.output)/'out.csv').write_text('value\\n'+a.threshold+'\\n')
"""
    result = run_isolated(
        code,
        {source: {"format": "csv", "content": b"a,b\n1,2\n"}},
        {"--input": source},
        parameters={"--threshold": "12"},
        timeout=20,
    )
    assert result["status"] == "SUCCEEDED", result
    with zipfile.ZipFile(io.BytesIO(result["archive"])) as archive:
        assert archive.read("result/out.csv") == b"value\n12\n"


def test_sandbox_limits_result_size():
    from executor.docker_runner import run_isolated

    source = uuid4()
    code = """import argparse
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--output'); a=p.parse_args()
Path(a.output).mkdir()
with (Path(a.output)/'large.bin').open('wb') as output:
 for _ in range(70): output.write(b'x' * 1024 * 1024)
"""
    result = run_isolated(
        code, {source: {"format": "csv", "content": b"a,b\n1,2\n"}}, {"--input": source}, timeout=20
    )
    assert result["status"] == "FAILED" and "archive" not in result
