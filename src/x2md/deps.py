"""Soft-detect optional external binaries used by markitdown plugins."""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class ExternalDep:
    binary: str
    purpose: str
    mac_hint: str
    win_hint: str
    linux_hint: str

    def install_hint(self) -> str:
        if sys.platform == "darwin":
            return self.mac_hint
        if sys.platform.startswith("win"):
            return self.win_hint
        return self.linux_hint


DEPS: tuple[ExternalDep, ...] = (
    ExternalDep(
        binary="ffmpeg",
        purpose="audio/video transcription",
        mac_hint="brew install ffmpeg",
        win_hint="winget install Gyan.FFmpeg  (or: choco install ffmpeg)",
        linux_hint="apt install ffmpeg  (or your distro equivalent)",
    ),
    ExternalDep(
        binary="tesseract",
        purpose="OCR for scanned PDFs / images",
        mac_hint="brew install tesseract",
        win_hint="winget install UB-Mannheim.TesseractOCR",
        linux_hint="apt install tesseract-ocr",
    ),
)


def missing_deps() -> list[ExternalDep]:
    return [d for d in DEPS if shutil.which(d.binary) is None]


def format_missing(deps: list[ExternalDep]) -> str:
    if not deps:
        return ""
    lines = ["Optional tools not found (features will degrade gracefully):"]
    for d in deps:
        lines.append(f"  - {d.binary}: needed for {d.purpose}")
        lines.append(f"      install: {d.install_hint()}")
    return "\n".join(lines)
