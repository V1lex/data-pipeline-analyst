"""Validate explicit quality rules against every row of a bounded file."""

import csv
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from file_data.profile import ProfileError, _flat
from file_data.rules import decimal_value, validate_rules


def validate_full_file(
    path: Path, filename: str, options: dict, required_columns: list[str], rules=None
) -> dict:
    rules = validate_rules(rules or [])
    fmt = Path(filename).suffix.lower().lstrip(".")
    missing_counts = {name: 0 for name in required_columns}
    missing_examples = {name: [] for name in required_columns}
    checked = 0
    rule_counts = [0] * len(rules)
    rule_examples = [[] for _ in rules]
    unique_values = [set() if rule["kind"] == "unique" else None for rule in rules]

    def check(row):
        nonlocal checked
        checked += 1
        for name in required_columns:
            value = row.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing_counts[name] += 1
                if len(missing_examples[name]) < 10:
                    missing_examples[name].append(checked)
        for index, rule in enumerate(rules):
            value = row.get(rule["column"])
            missing = value is None or (isinstance(value, str) and not value.strip())
            kind = rule["kind"]
            invalid = missing if kind == "not_null" else False
            if not missing:
                if kind == "unique":
                    token = str(value)
                    invalid = token in unique_values[index]
                    unique_values[index].add(token)
                elif kind == "one_of":
                    invalid = str(value) not in rule["values"]
                elif kind == "date":
                    try:
                        text = str(value)
                        invalid = len(text) != 10 or date.fromisoformat(text).isoformat() != text
                    except ValueError:
                        invalid = True
                elif kind in {"number", "gt", "gte", "lt", "lte"}:
                    numeric = decimal_value(value)
                    invalid = numeric is None
                    if not invalid and kind != "number":
                        bound = decimal_value(rule["value"])
                        invalid = not {
                            "gt": numeric > bound,
                            "gte": numeric >= bound,
                            "lt": numeric < bound,
                            "lte": numeric <= bound,
                        }[kind]
            if invalid:
                rule_counts[index] += 1
                if len(rule_examples[index]) < 10:
                    rule_examples[index].append(checked)

    try:
        if fmt == "parquet":
            import pyarrow.parquet as pq

            parquet = pq.ParquetFile(path)
            names = set(parquet.schema_arrow.names)
            for batch in parquet.iter_batches(batch_size=512):
                for row in batch.to_pylist():
                    check(_flat(row, checked + 1))
        elif fmt == "csv":
            encoding = options.get("encoding", "utf-8-sig")
            delimiter = options.get("delimiter", ",")
            with path.open(encoding=encoding, newline="") as stream:
                reader = csv.reader(stream, delimiter=delimiter, strict=True)
                header = next(reader, [])
                names = set(header)
                for values in reader:
                    if len(values) != len(header):
                        raise ProfileError("Некорректное число полей в строке.")
                    check(dict(zip(header, values, strict=True)))
        elif fmt == "json":
            with path.open(encoding=options.get("encoding", "utf-8-sig")) as stream:
                data = json.load(stream)
            if not isinstance(data, list):
                raise ProfileError("JSON должен содержать массив объектов.")
            names = set()
            for index, value in enumerate(data, 1):
                row = _flat(value, index)
                names.update(row)
                check(row)
        elif fmt == "jsonl":
            names = set()
            with path.open(encoding=options.get("encoding", "utf-8-sig")) as stream:
                for index, line in enumerate(stream, 1):
                    if line.strip():
                        row = _flat(json.loads(line), index)
                        names.update(row)
                        check(row)
        else:
            raise ProfileError("Этот формат не поддерживается.")
    except (ValueError, csv.Error, UnicodeError) as exc:
        if isinstance(exc, ProfileError):
            raise
        raise ProfileError("Не удалось прочитать файл для проверки.") from None
    missing_columns = sorted((set(required_columns) | {rule["column"] for rule in rules}) - names)
    errors = [{"type": "MISSING_COLUMN", "column": name} for name in missing_columns]
    errors += [
        {
            "type": "EMPTY_VALUES",
            "column": name,
            "count": count,
            "example_rows": missing_examples[name],
        }
        for name, count in missing_counts.items()
        if count and name not in missing_columns
    ]
    if not checked:
        errors.append({"type": "EMPTY_FILE"})
    rule_errors = [
        {
            "rule": rule["kind"],
            "column": rule["column"],
            "count": rule_counts[index],
            "example_rows": rule_examples[index],
        }
        for index, rule in enumerate(rules)
        if rule_counts[index] and rule["column"] not in missing_columns
    ]
    result = {"valid": not errors and not rule_errors, "checked_rows": checked, "errors": errors}
    if rules:
        result["rule_errors"] = rule_errors
        result["checked_rules"] = rules
    return result


def validate_in_subprocess(
    path: Path, filename: str, options: dict, required_columns: list[str], rules=None
) -> dict:
    env = {
        "PATH": os.defpath,
        "PYTHONUTF8": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    try:
        result = subprocess.run(
            [sys.executable, "-m", "file_data.validation"],
            input=json.dumps(
                {
                    "path": str(path),
                    "filename": filename,
                    "options": options,
                    "required_columns": required_columns,
                    "rules": rules or [],
                }
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            env=env,
            check=False,
        )
        payload = json.loads(result.stdout)
        if result.returncode or "error" in payload:
            raise ProfileError(payload.get("error", "Проверка файла не завершилась."))
        return payload
    except ProfileError:
        raise
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise ProfileError("Проверка файла не завершилась в пределах ограничений.") from None


def main():
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    try:
        args = json.load(sys.stdin)
        print(
            json.dumps(
                validate_full_file(
                    Path(args["path"]),
                    args["filename"],
                    args["options"],
                    args["required_columns"],
                    args.get("rules", []),
                ),
                ensure_ascii=False,
            )
        )
    except ProfileError:
        print(
            json.dumps(
                {"valid": False, "checked_rows": 0, "errors": [{"type": "FILE_FORMAT_INVALID"}]}
            )
        )
        return 0
    except Exception:
        print(json.dumps({"error": "Не удалось безопасно проверить файл."}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
