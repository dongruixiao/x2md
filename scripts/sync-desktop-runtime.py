#!/usr/bin/env python3
"""Install the current x2md package into the bundled desktop runtime."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

DEFAULT_RUNTIME = Path("apps/desktop/src-tauri/resources/x2md-runtime")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def runtime_python(runtime: Path) -> Path:
    if (runtime / "python" / "bin" / "python3").exists():
        return runtime / "python" / "bin" / "python3"
    if (runtime / "python" / "bin" / "python").exists():
        return runtime / "python" / "bin" / "python"
    if (runtime / "Scripts" / "python.exe").exists():
        return runtime / "Scripts" / "python.exe"
    if (runtime / "python" / "python.exe").exists():
        return runtime / "python" / "python.exe"
    raise SystemExit(f"bundled runtime Python not found under {runtime}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync current x2md sources into the desktop runtime.")
    parser.add_argument("--runtime", type=Path, default=repo_root() / DEFAULT_RUNTIME)
    args = parser.parse_args()

    root = repo_root()
    python = runtime_python(args.runtime.resolve())
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--force-reinstall",
            str(root),
        ],
        cwd=root,
        check=True,
    )
    subprocess.run([str(python), "-m", "x2md", "--version"], cwd=root, check=True)
    print(f"Synced x2md into desktop runtime: {python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
