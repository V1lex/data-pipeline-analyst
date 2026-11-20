"""Trusted container entry: run one supplied artifact without network or credentials."""

import base64
import io
import json
import os
import re
import signal
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

MAX_INPUT = 210 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
INPUT = Path("/input")
OUTPUT = Path("/output")


def main():
    total = 0
    names = set()
    try:
        with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as archive:
            for member in archive:
                if not member.isfile() or not re.fullmatch(
                    r"(config\.json|program\.py|[0-9a-f]{32}\.(csv|json|jsonl|parquet))",
                    member.name,
                ):
                    raise ValueError("Недопустимое имя входного файла")
                if member.name in names:
                    raise ValueError("Повтор входного файла")
                names.add(member.name)
                total += member.size
                if total > MAX_INPUT:
                    raise ValueError("Превышен размер входных данных")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("Не удалось прочитать входной файл")
                target = INPUT / member.name
                with target.open("xb") as output:
                    while chunk := source.read(65536):
                        output.write(chunk)
                target.chmod(0o444)
        config = json.loads((INPUT / "config.json").read_text())
        mapping = config["inputs"]
        timeout = int(config.get("timeout", 120))
        if not 1 <= timeout <= 300 or not isinstance(mapping, dict) or not mapping:
            raise ValueError("Некорректные параметры запуска")
        args = []
        for flag, name in mapping.items():
            if (
                not re.fullmatch(r"--[a-z][a-z0-9-]{0,40}", flag)
                or flag == "--output"
                or name not in names
                or name in {"config.json", "program.py"}
            ):
                raise ValueError("Некорректные параметры источников")
            args.extend((flag, str(INPUT / name)))
        parameters = config.get("parameters", {})
        if (
            not isinstance(parameters, dict)
            or len(parameters) > 20
            or set(parameters) & set(mapping)
        ):
            raise ValueError("Некорректные дополнительные параметры")
        for flag, value in parameters.items():
            if (
                not re.fullmatch(r"--[a-z][a-z0-9-]{0,40}", flag)
                or flag == "--output"
                or not isinstance(value, str)
                or len(value) > 512
                or "\x00" in value
            ):
                raise ValueError("Некорректный дополнительный параметр")
            args.extend((flag, value))
        INPUT.chmod(0o555)
        OUTPUT.chmod(0o1777)
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "SPARK_LOCAL_IP": "127.0.0.1",
            "PYSPARK_PYTHON": sys.executable,
            "PYTHONDONTWRITEBYTECODE": "1",
        }

        def drop_privileges():
            os.setgroups([])
            os.setgid(65534)
            os.setuid(65534)

        with open("/tmp/program.log", "wb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(INPUT / "program.py"),
                    *args,
                    "--output",
                    str(OUTPUT / "result"),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                env=env,
                start_new_session=True,
                preexec_fn=drop_privileges,
            )
            try:
                code = process.wait(timeout=timeout)
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                print(json.dumps({"status": "TIMEOUT", "error": "Превышено время выполнения."}))
                return
        if code:
            with open("/tmp/program.log", "rb") as log:
                log.seek(0, os.SEEK_END)
                log.seek(max(0, log.tell() - 4000))
                detail = log.read().decode("utf-8", errors="replace")
            print(
                json.dumps(
                    {
                        "status": "FAILED",
                        "error": "Скрипт завершился с ошибкой.",
                        "exit_code": code,
                        "diagnostic": detail,
                    }
                )
            )
            return
        buffer = io.BytesIO()
        count = 0
        total = 0
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as result:
            for root, dirs, files in os.walk(OUTPUT, followlinks=False):
                if any((Path(root) / name).is_symlink() for name in dirs + files):
                    raise ValueError("Символические ссылки в результате не поддерживаются")
                for name in files:
                    file = Path(root) / name
                    total += file.stat().st_size
                    count += 1
                    if total > MAX_OUTPUT or count > 1000:
                        raise ValueError("Превышен предел размера результата")
                    result.write(file, file.relative_to(OUTPUT).as_posix())
        if not count:
            raise ValueError("Скрипт не создал файлы результата")
        print(
            json.dumps(
                {
                    "status": "SUCCEEDED",
                    "archive": base64.b64encode(buffer.getvalue()).decode("ascii"),
                }
            )
        )
    except Exception:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error": "Не удалось безопасно выполнить скрипт или сохранить результат.",
                }
            )
        )


if __name__ == "__main__":
    main()
