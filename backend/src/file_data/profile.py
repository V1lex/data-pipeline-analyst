"""Profile a bounded sample without treating inferred values as a schema guarantee."""

import csv
import json
import math
from pathlib import Path

MAX_SAMPLE_ROWS = 1000
MAX_COLUMNS = 200
MAX_CELL_CHARS = 1000


class ProfileError(ValueError):
    """A source cannot be read as a supported flat table."""


def _flat(row, line):
    if not isinstance(row, dict):
        raise ProfileError(f"Строка {line}: ожидается JSON-объект.")
    if len(row) > MAX_COLUMNS:
        raise ProfileError("Слишком много колонок.")
    if any(isinstance(v, (dict, list)) for v in row.values()):
        raise ProfileError(f"Строка {line}: вложенные объекты и массивы не поддерживаются.")
    if any(not isinstance(k, str) or not k.strip() or len(k) > 256 for k in row):
        raise ProfileError(f"Строка {line}: имена колонок должны быть непустыми строками.")
    return row


def _json_value(value):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)[:MAX_CELL_CHARS]


def _infer(values):
    values = [value for value in values if value is not None and value != ""]
    if not values:
        return "unknown"
    lowered = [str(value).lower() for value in values]
    if all(value in {"true", "false"} for value in lowered):
        return "boolean"
    try:
        for value in values:
            int(str(value))
        return "integer"
    except ValueError:
        pass
    try:
        for value in values:
            if not math.isfinite(float(str(value))):
                raise ValueError
        return "number"
    except ValueError:
        return "string"


def profile_file(path: Path, filename: str, checksum: str, options: dict | None = None) -> dict:
    options = options or {}
    fmt = Path(filename).suffix.lower().lstrip(".")
    if fmt not in {"csv", "json", "jsonl", "parquet"}:
        raise ProfileError("Поддерживаются CSV, JSON, JSONL и Parquet.")
    rows = []
    schema = None
    read_options = {}
    try:
        if fmt == "parquet":
            import pyarrow as pa
            import pyarrow.parquet as pq

            parquet = pq.ParquetFile(path)
            if len(parquet.schema_arrow) > MAX_COLUMNS:
                raise ProfileError("Слишком много колонок.")
            if any(pa.types.is_nested(field.type) for field in parquet.schema_arrow):
                raise ProfileError("Вложенные колонки Parquet не поддерживаются.")
            schema = [(field.name, str(field.type)) for field in parquet.schema_arrow]
            batch = next(parquet.iter_batches(batch_size=MAX_SAMPLE_ROWS), None)
            rows = batch.to_pylist() if batch else []
        else:
            encoding = options.get("encoding")
            if not encoding:
                raw = path.read_bytes()
                try:
                    raw.decode("utf-8-sig")
                    encoding = "utf-8-sig"
                except UnicodeDecodeError:
                    raw.decode("cp1251")
                    encoding = "cp1251"
            if encoding not in {"utf-8", "utf-8-sig", "cp1251"}:
                raise ProfileError("Неподдерживаемая кодировка.")
            read_options["encoding"] = encoding
            if fmt == "csv":
                with path.open(encoding=encoding, newline="") as source:
                    sample = source.read(16384)
                    source.seek(0)
                    delimiter = options.get("delimiter")
                    if delimiter is None:
                        try:
                            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
                        except csv.Error:
                            # A table with a single column has no delimiter to detect.
                            # Inconsistent multi-column records still fail validation below.
                            delimiter = ","
                    if delimiter not in {",", ";", "\t", "|"}:
                        raise ProfileError("Неподдерживаемый разделитель CSV.")
                    read_options["delimiter"] = delimiter
                    reader = csv.reader(source, delimiter=delimiter, strict=True)
                    headers = next(reader, [])
                    if (
                        not headers
                        or any(not h.strip() or len(h) > 256 for h in headers)
                        or len(headers) != len(set(headers))
                        or len(headers) > MAX_COLUMNS
                    ):
                        raise ProfileError("Заголовки CSV должны быть непустыми и уникальными.")
                    schema = [(header, None) for header in headers]
                    for index, values in enumerate(reader, 2):
                        if len(values) != len(headers):
                            raise ProfileError(
                                f"Строка {index}: число полей не совпадает с заголовком."
                            )
                        if len(rows) < MAX_SAMPLE_ROWS:
                            rows.append(dict(zip(headers, values, strict=True)))
            elif fmt == "json":
                with path.open(encoding=encoding) as source:
                    data = json.load(source)
                if not isinstance(data, list):
                    raise ProfileError("JSON должен содержать массив плоских объектов.")
                for index, row in enumerate(data):
                    row = _flat(row, index + 1)
                    if len(rows) < MAX_SAMPLE_ROWS:
                        rows.append(row)
            else:
                with path.open(encoding=encoding) as source:
                    for index, line in enumerate(source, 1):
                        if line.strip():
                            row = _flat(json.loads(line), index)
                            if len(rows) < MAX_SAMPLE_ROWS:
                                rows.append(row)
    except ProfileError:
        raise
    except (OSError, ValueError, csv.Error, UnicodeError) as exc:
        raise ProfileError(f"Не удалось прочитать {fmt.upper()}: {type(exc).__name__}.") from None
    if schema is None:
        names = list(dict.fromkeys(name for row in rows for name in row))
        if len(names) > MAX_COLUMNS:
            raise ProfileError("Слишком много колонок.")
        schema = [(name, None) for name in names]
    if not schema:
        raise ProfileError("Не найдены колонки или записи.")
    columns = []
    for name, declared in schema:
        values = [row.get(name) for row in rows]
        columns.append(
            {
                "name": name,
                "type": declared or _infer(values),
                "type_source": "parquet_schema" if declared else "inferred_sample",
                "null_count": sum(value is None or value == "" for value in values),
            }
        )
    return {
        "checksum": checksum,
        "format": fmt,
        "read_options": read_options,
        "columns": columns,
        "sample": [{name: _json_value(row.get(name)) for name, _ in schema} for row in rows[:20]],
        "sampled_rows": len(rows),
        "scope": "sample",
        "warnings": ["Типы и пропуски определены по выборке; это не гарантия для всего файла."],
    }


def profile_in_subprocess(
    path: Path, filename: str, checksum: str, options: dict | None = None
) -> dict:
    """Run untrusted parsing in a short-lived process with a restricted environment."""
    import os
    import subprocess
    import sys

    env = {
        "PATH": os.defpath,
        "PYTHONUTF8": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    try:
        result = subprocess.run(
            [sys.executable, "-m", "file_data.profile_worker"],
            input=json.dumps(
                {
                    "path": str(path),
                    "filename": filename,
                    "checksum": checksum,
                    "options": options or {},
                }
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            env=env,
            check=False,
        )
        payload = json.loads(result.stdout)
        if result.returncode or "error" in payload:
            raise ProfileError(payload.get("error", "Не удалось безопасно прочитать файл."))
        return payload
    except ProfileError:
        raise
    except (subprocess.TimeoutExpired, ValueError, OSError):
        raise ProfileError("Профилирование не завершилось в пределах ограничений.") from None
