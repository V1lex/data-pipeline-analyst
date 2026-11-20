"""Bounded, isolated parsing of result archives and optional CSV export."""

import csv
import json
import math
import resource
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

MAX_RESULT_BYTES = 64 * 1024 * 1024


class LimitedWriter:
    def __init__(self, target):
        self.target = target
        self.size = 0

    def write(self, value):
        self.size += len(value.encode("utf-8"))
        if self.size > MAX_RESULT_BYTES:
            raise ValueError("CSV export exceeds limit")
        return self.target.write(value)


def read_result(archive_path: Path, directory: Path) -> dict:
    from file_data.profile import _json_value, profile_file

    extracted = directory / "files"
    extracted.mkdir()
    total = 0
    names = set()
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) > 1000:
            raise ValueError("Too many output files")
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or entry.filename in names
                or stat.S_ISLNK(entry.external_attr >> 16)
            ):
                raise ValueError("Invalid output path")
            names.add(entry.filename)
            total += entry.file_size
            if total > MAX_RESULT_BYTES:
                raise ValueError("Output exceeds limit")
            target = extracted.joinpath(*path.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("xb") as output:
                while chunk := source.read(65536):
                    output.write(chunk)
    paths = sorted(
        p
        for p in extracted.rglob("*")
        if p.is_file() and p.suffix.lower() in {".parquet", ".csv", ".jsonl", ".json"}
    )
    if not paths:
        return {"warning": "Архив сохранён, но табличные файлы для предпросмотра не найдены."}
    first = paths[0]
    options = {"delimiter": ","} if first.suffix.lower() == ".csv" else {}
    profile = profile_file(first, first.name, "", options)
    preview = {
        "columns": profile["columns"],
        "rows": profile["sample"],
        "scope": "sample",
        "warning": "Предпросмотр содержит до 20 строк результата; набор может быть неполным.",
    }
    if first.suffix.lower() == ".csv":
        csv_path = directory / "result.csv"
        preview["rows"] = []
        try:
            with csv_path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.writer(LimitedWriter(output))
                header = None
                for path in (item for item in paths if item.suffix.lower() == ".csv"):
                    with path.open(encoding="utf-8-sig", newline="") as source:
                        reader = csv.reader(source, strict=True)
                        current = next(reader, [])
                        if header is None:
                            header = current
                            writer.writerow(header)
                        elif current != header:
                            raise ValueError("Incompatible CSV headers")
                        for row in reader:
                            if len(row) != len(header):
                                raise ValueError("Invalid CSV row")
                            if len(preview["rows"]) < 20:
                                preview["rows"].append(dict(zip(header, row, strict=True)))
                            writer.writerow(row)
            preview["csv_available"] = True
        except (OSError, ValueError, csv.Error):
            csv_path.unlink(missing_ok=True)
            preview["warning"] += " CSV-экспорт недоступен из-за ограничений размера или схемы."
        return preview
    if first.suffix.lower() != ".parquet":
        return preview
    import pyarrow.parquet as pq

    parquet_paths = [p for p in paths if p.suffix.lower() == ".parquet"]
    csv_path = directory / "result.csv"
    preview["rows"] = []
    try:
        with csv_path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(LimitedWriter(output))
            columns = None
            for path in parquet_paths:
                parquet = pq.ParquetFile(path)
                current = parquet.schema_arrow.names
                if columns is None:
                    columns = current
                    writer.writerow(columns)
                elif current != columns:
                    raise ValueError("Несовместимые схемы файлов результата")
                for batch in parquet.iter_batches(batch_size=512):
                    for row in batch.to_pylist():
                        if len(preview["rows"]) < 20:
                            preview["rows"].append(
                                {name: _json_value(row.get(name)) for name in columns}
                            )
                        values = []
                        for name in columns:
                            value = row.get(name)
                            if isinstance(value, float) and not math.isfinite(value):
                                value = ""
                            values.append(value)
                        writer.writerow(values)
        preview["csv_available"] = True
    except (ValueError, OSError):
        csv_path.unlink(missing_ok=True)
        preview["warning"] += " CSV-экспорт недоступен из-за ограничений размера или схемы."
    return preview


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
    if sys.platform.startswith("linux"):
        limit = 2 * 1024 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    try:
        args = json.load(sys.stdin)
        print(
            json.dumps(
                read_result(Path(args["archive"]), Path(args["directory"])),
                ensure_ascii=False,
                default=str,
                allow_nan=False,
            )
        )
    except Exception:
        print(
            json.dumps(
                {"warning": "Не удалось построить предпросмотр результата."}, ensure_ascii=False
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
