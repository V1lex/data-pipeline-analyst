import subprocess
import sys
from pathlib import Path


def test_launcher_finds_executor_when_python_starts_outside_project(tmp_path):
    launcher = Path(__file__).resolve().parents[1] / "scripts" / "run_executor.py"
    program = """
import runpy, sys, types
fake = types.ModuleType('execution.worker')
def main():
    import executor.docker_runner
    print('EXECUTOR_IMPORT_OK')
fake.main = main
sys.modules['execution.worker'] = fake
runpy.run_path(sys.argv[1], run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", program, str(launcher)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "EXECUTOR_IMPORT_OK" in result.stdout
