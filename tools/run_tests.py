"""Run pytest with a hard process deadline on Windows, macOS and Linux."""
import os
from pathlib import Path
import subprocess
import sys


def run(command, timeout=180):
    try:
        return subprocess.run(command, timeout=timeout, check=False).returncode
    except subprocess.TimeoutExpired:
        print(f"Test process exceeded {timeout} seconds and was terminated.", file=sys.stderr, flush=True)
        return 124


def main():
    # UTF-8 avoids locale-specific failures rendering INR and validation messages.
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONFAULTHANDLER", "1")
    root=Path(__file__).resolve().parents[1]
    command=[sys.executable,"-m","pytest","-vv",*sys.argv[1:]]
    os.chdir(root)
    return run(command)


if __name__=="__main__":
    raise SystemExit(main())
