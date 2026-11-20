"""Launch a single generated artifact with only its explicitly selected inputs."""

import base64
import binascii
import io
import json
import re
import subprocess
import tarfile
from uuid import UUID, uuid4

MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def build_bundle(
    code: str, sources: dict, mapping: dict, timeout: int = 120, *, parameters: dict | None = None
) -> bytes:
    if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Некорректный артефакт")
    if not 1 <= timeout <= 300 or not sources or len(sources) > 20:
        raise ValueError("Некорректные параметры запуска")
    if len(set(mapping.values())) != len(mapping) or set(mapping.values()) != set(sources):
        raise ValueError("Нужны аргументы для всех выбранных источников")
    for flag in mapping:
        if not re.fullmatch(r"--[a-z][a-z0-9-]{0,40}", flag) or flag == "--output":
            raise ValueError("Недопустимое имя аргумента")
    parameters = parameters or {}
    if len(parameters) > 20 or set(parameters) & set(mapping):
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
    names = {}
    total = 0
    for key, source in sources.items():
        if (
            not isinstance(key, UUID)
            or source["format"] not in {"csv", "json", "jsonl", "parquet"}
            or not isinstance(source["content"], bytes)
        ):
            raise ValueError("Некорректный источник")
        total += len(source["content"])
        if total > MAX_INPUT_BYTES:
            raise ValueError("Входные данные превышают допустимый размер")
        names[key] = f"{key.hex}.{source['format']}"
    config = json.dumps(
        {
            "inputs": {flag: names[file_id] for flag, file_id in mapping.items()},
            "timeout": timeout,
            "parameters": parameters,
        }
    ).encode()
    entries = {"config.json": config, "program.py": code.encode("utf-8")}
    entries.update({names[key]: source["content"] for key, source in sources.items()})
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as archive:
        for name, content in entries.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            entry.mode = 0o444
            archive.addfile(entry, io.BytesIO(content))
    return result.getvalue()


def run_isolated(
    code: str,
    sources: dict,
    mapping: dict,
    *,
    timeout: int = 120,
    parameters: dict | None = None,
    image: str = "dpa-spark-runner:local",
) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/:@-]{0,200}", image):
        raise ValueError("Некорректное имя образа")
    bundle = build_bundle(code, sources, mapping, timeout, parameters=parameters)
    container = "dpa-run-" + uuid4().hex
    command = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "-i",
        "--name",
        container,
        "--network",
        "none",
        "--read-only",
        "--memory",
        "2g",
        "--cpus",
        "1",
        "--pids-limit",
        "192",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "SETUID",
        "--cap-add",
        "SETGID",
        "--cap-add",
        "KILL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/input:rw,size=220m,mode=0755",
        "--tmpfs",
        "/output:rw,size=64m,mode=1777",
        "--tmpfs",
        "/tmp:rw,exec,size=128m,mode=1777",
        image,
    ]
    try:
        result = subprocess.run(
            command, input=bundle, capture_output=True, timeout=timeout + 30, check=False
        )
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(
                ["docker", "rm", "-f", container], capture_output=True, timeout=15, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        return {"status": "TIMEOUT", "error": "Превышено время выполнения."}
    except OSError:
        return {"status": "FAILED", "error": "Изолированное окружение недоступно."}
    if result.returncode != 0 or len(result.stdout) > MAX_ARCHIVE_BYTES * 2:
        return {"status": "FAILED", "error": "Изолированное окружение завершилось с ошибкой."}
    try:
        response = json.loads(result.stdout)
        if response.get("status") == "SUCCEEDED":
            archive = base64.b64decode(response.pop("archive"), validate=True)
            if len(archive) > MAX_ARCHIVE_BYTES:
                raise ValueError
            response["archive"] = archive
        elif response.get("status") not in {"FAILED", "TIMEOUT"}:
            raise ValueError
        return response
    except (ValueError, KeyError, TypeError, binascii.Error):
        return {"status": "FAILED", "error": "Некорректный результат изолированного окружения."}
