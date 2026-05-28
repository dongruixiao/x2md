"""Cross-platform I/O helpers.

Centralizes the three things Windows tends to get wrong:
- stdout/stderr default encoding (cp1252/gbk instead of utf-8)
- mixed path separators and non-ASCII paths
- CRLF line endings sneaking into generated files
"""
from __future__ import annotations

import sys
from pathlib import Path


def configure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def write_stdout(content: str) -> None:
    sys.stdout.write(content)
    if not content.endswith("\n"):
        sys.stdout.write("\n")


def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def default_output_path(input_path: Path) -> Path:
    return input_path.with_suffix(".md")


def mirror_output_path(input_file: Path, input_root: Path, output_root: Path) -> Path:
    rel = input_file.relative_to(input_root)
    return (output_root / rel).with_suffix(".md")
