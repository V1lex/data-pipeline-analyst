"""Short-lived bounded parser process; reads request from stdin."""

import json
import resource
import sys
from pathlib import Path

from file_data.profile import ProfileError, profile_file


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    if sys.platform.startswith("linux"):
        limit = 2 * 1024 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    try:
        data = json.load(sys.stdin)
        result = profile_file(
            Path(data["path"]), data["filename"], data["checksum"], data["options"]
        )
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except ProfileError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1
    except Exception:
        print(json.dumps({"error": "Не удалось безопасно прочитать файл."}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
