#!/usr/bin/env python3
"""Build the bundled Python runtime used by the Tauri desktop app."""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

REPO = "astral-sh/python-build-standalone"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DEFAULT_PYTHON_MINOR = "3.12"
DEFAULT_OUTPUT = Path("apps/desktop/src-tauri/resources/x2md-runtime")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def target_triple() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "aarch64-apple-darwin"
    raise SystemExit(f"unsupported desktop runtime target: {system} {machine}; first runtime target is macOS arm64")


def fetch_latest_release() -> dict:
    with urllib.request.urlopen(API_URL, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def select_asset(release: dict, python_minor: str, triple: str) -> tuple[str, str]:
    candidates = []
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if (
            name.startswith(f"cpython-{python_minor}.")
            and triple in name
            and name.endswith("install_only.tar.gz")
            and "stripped" not in name
        ):
            candidates.append((name, asset["browser_download_url"]))
    if not candidates:
        raise SystemExit(f"no python-build-standalone asset found for Python {python_minor} and {triple}")
    return sorted(candidates)[-1]


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)


def extract_python(archive: Path, output: Path) -> Path:
    with tempfile.TemporaryDirectory(prefix="x2md-runtime-extract-") as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmp_path)
        source = tmp_path / "python"
        if not source.exists():
            matches = [path for path in tmp_path.rglob("python") if path.is_dir() and (path / "bin").exists()]
            if not matches:
                raise SystemExit("downloaded runtime archive did not contain a python directory")
            source = matches[0]
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), output / "python")
    python = output / "python" / "bin" / "python3"
    if not python.exists():
        raise SystemExit(f"runtime python not found at {python}")
    return python


def run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def run_with_retry(cmd: list[str], cwd: Path, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        try:
            run(cmd, cwd)
            return
        except subprocess.CalledProcessError:
            if attempt == attempts:
                raise
            print(f"Command failed; retrying ({attempt + 1}/{attempts})")


def build_runtime(output: Path, python_minor: str, keep_archive: bool) -> None:
    root = repo_root()
    triple = target_triple()
    release = fetch_latest_release()
    asset_name, asset_url = select_asset(release, python_minor, triple)

    with tempfile.TemporaryDirectory(prefix="x2md-runtime-build-") as tmp:
        archive = Path(tmp) / asset_name
        print(f"Downloading {asset_name}")
        download(asset_url, archive)
        python = extract_python(archive, output)
        if keep_archive:
            shutil.copy2(archive, output / asset_name)

    run([str(python), "-m", "ensurepip", "--upgrade"], root)
    pip_base = [str(python), "-m", "pip", "--retries", "5", "--timeout", "120"]
    run_with_retry([*pip_base, "install", "--upgrade", "pip", "wheel", "setuptools"], root)
    run_with_retry([*pip_base, "install", f"{root}[desktop]"], root)
    run([str(python), "-m", "x2md", "--version"], root)

    print(f"Built desktop runtime at {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build bundled x2md desktop Python runtime.")
    parser.add_argument("--output", type=Path, default=repo_root() / DEFAULT_OUTPUT)
    parser.add_argument("--python-minor", default=DEFAULT_PYTHON_MINOR)
    parser.add_argument("--keep-archive", action="store_true")
    args = parser.parse_args(argv)

    build_runtime(args.output.resolve(), args.python_minor, args.keep_archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
