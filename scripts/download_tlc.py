"""Install the pinned official TLC release; verify before replacing the local jar."""

import hashlib
import urllib.request
from pathlib import Path

URL = "https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar"
SHA256 = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"


def main():
    destination = Path(__file__).resolve().parents[1] / "worker/tla2tools.jar"
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == SHA256:
        print("TLC v1.7.4 already verified")
        return
    with urllib.request.urlopen(URL, timeout=60) as response:
        data = response.read(8 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError("TLC download checksum does not match; existing jar was preserved")
    destination.parent.mkdir(exist_ok=True)
    temporary = destination.with_suffix(".download")
    temporary.write_bytes(data)
    temporary.replace(destination)
    print("TLC v1.7.4 downloaded and verified")


if __name__ == "__main__":
    main()
