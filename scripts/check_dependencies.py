"""Validate latest Hermes dependencies using its own lock-file override policy."""

import subprocess
import sys
from pathlib import Path

from pm.pip_check import unexpected_incompatibilities


def main():
    result = subprocess.run(
        [sys.argv[1], "pip", "check", "--python", sys.executable],
        capture_output=True, text=True, check=False,
    )
    report = result.stdout + result.stderr
    if result.returncode:
        unexpected = unexpected_incompatibilities(report, Path(sys.argv[2]))
        if unexpected != []:
            raise SystemExit(report)
        print("Dependency check: only overrides recorded in the official Hermes lock")
    else:
        print("Dependency check: OK")


if __name__ == "__main__":
    main()
