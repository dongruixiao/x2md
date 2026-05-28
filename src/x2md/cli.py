"""x2md command-line entry point."""
from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from .converter import (
    BACKENDS,
    Backend,
    BackendOptions,
    ConversionError,
    ConversionResult,
    convert_file_result,
    convert_stream_result,
    convert_url,
    relocate_resources,
)
from .deps import format_missing, missing_deps
from .io_utils import (
    configure_stdio_utf8,
    default_output_path,
    is_url,
    mirror_output_path,
    write_stdout,
    write_text,
)

SUPPORTED_SUFFIXES = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls",
    ".html", ".htm", ".csv", ".json", ".xml",
    ".epub", ".zip",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff",
    ".mp3", ".wav", ".m4a",
    ".txt", ".md",
}

MINERU_BACKENDS = (
    "pipeline",
    "vlm-http-client",
    "hybrid-http-client",
    "vlm-auto-engine",
    "hybrid-auto-engine",
)
MINERU_METHODS = ("auto", "txt", "ocr")
MINERU_LANGS = (
    "ch",
    "ch_server",
    "ch_lite",
    "en",
    "korean",
    "japan",
    "chinese_cht",
    "ta",
    "te",
    "ka",
    "th",
    "el",
    "latin",
    "arabic",
    "east_slavic",
    "cyrillic",
    "devanagari",
)
DOCLING_PIPELINES = ("legacy", "standard", "vlm", "asr")
DOCLING_IMAGE_EXPORT_MODES = ("placeholder", "embedded", "referenced")
DOCLING_TABLE_MODES = ("fast", "accurate")
DOCLING_DEVICES = ("auto", "cpu", "cuda", "mps", "xpu")
QUALITY_MODES = ("fast", "balanced", "best")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="x2md",
        description="Convert documents to Markdown.",
    )
    p.add_argument("input", nargs="?", help="file, directory, URL, or '-' for stdin")
    p.add_argument("-o", "--output", help="output file (single-input mode)")
    p.add_argument("-O", "--output-dir", help="output directory (directory mode)")
    p.add_argument("-r", "--recursive", action="store_true", help="recurse into subdirectories")
    p.add_argument("--skip-existing", action="store_true", help="skip files whose output Markdown already exists")
    p.add_argument("-f", "--format", help="input format for stdin, e.g. 'docx' or '.pdf'")
    p.add_argument("--host", default="127.0.0.1", help=argparse.SUPPRESS)
    p.add_argument("--port", type=int, default=8765, help=argparse.SUPPRESS)
    p.add_argument("--quality", choices=QUALITY_MODES, default="best", help="conversion quality/speed tradeoff")
    p.add_argument("--ocr", action="store_true", help="enable OCR for scanned or image-only documents")
    p.add_argument("--remove-watermark", action="store_true", help="remove repeated watermark text where possible")
    p.add_argument("--language", default="ch", help="document language hint for OCR, e.g. ch or en")
    p.add_argument("--charts", action=argparse.BooleanOptionalAction, default=None, help="enable/disable chart and image analysis")
    p.add_argument("--start-page", type=int, help="first PDF page to process, 0-based")
    p.add_argument("--end-page", type=int, help="last PDF page to process, 0-based")
    p.add_argument(
        "--backend",
        choices=BACKENDS,
        default="auto",
        help=argparse.SUPPRESS,
    )
    p.add_argument("--quiet", action="store_true", help="suppress backend progress logs where supported")
    p.add_argument("--mineru-backend", choices=MINERU_BACKENDS, help=argparse.SUPPRESS)
    p.add_argument("--mineru-method", choices=MINERU_METHODS, help=argparse.SUPPRESS)
    p.add_argument("--mineru-lang", choices=MINERU_LANGS, help=argparse.SUPPRESS)
    p.add_argument("--no-formula", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--no-table", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--no-image-analysis", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--mineru-api-url", help=argparse.SUPPRESS)
    p.add_argument("--mineru-server-url", help=argparse.SUPPRESS)
    p.add_argument("--docling-pipeline", choices=DOCLING_PIPELINES, help=argparse.SUPPRESS)
    p.add_argument("--docling-vlm-model", help=argparse.SUPPRESS)
    p.add_argument(
        "--docling-image-export-mode",
        choices=DOCLING_IMAGE_EXPORT_MODES,
        help=argparse.SUPPRESS,
    )
    p.add_argument("--docling-ocr", action=argparse.BooleanOptionalAction, default=None, help=argparse.SUPPRESS)
    p.add_argument(
        "--docling-force-ocr",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--docling-tables",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=argparse.SUPPRESS,
    )
    p.add_argument("--docling-ocr-engine", help=argparse.SUPPRESS)
    p.add_argument("--docling-ocr-lang", help=argparse.SUPPRESS)
    p.add_argument("--docling-table-mode", choices=DOCLING_TABLE_MODES, help=argparse.SUPPRESS)
    p.add_argument("--docling-enrich-picture-classes", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--docling-enrich-picture-description", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--docling-enrich-chart-extraction", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--docling-device", choices=DOCLING_DEVICES, help=argparse.SUPPRESS)
    p.add_argument("--docling-num-threads", type=int, help=argparse.SUPPRESS)
    p.add_argument("--check-deps", action="store_true", help="report optional external tools and exit")
    p.add_argument("-V", "--version", action="version", version=f"x2md {__version__}")
    return p


def _backend_options(args: argparse.Namespace) -> BackendOptions:
    mineru_backend = args.mineru_backend
    mineru_method = args.mineru_method
    mineru_lang = args.mineru_lang or args.language
    mineru_image_analysis = False if args.no_image_analysis else None
    docling_ocr = args.docling_ocr
    docling_force_ocr = args.docling_force_ocr
    docling_table_mode = args.docling_table_mode
    docling_image_export_mode = args.docling_image_export_mode
    docling_ocr_lang = args.docling_ocr_lang or args.language
    docling_enrich_picture_description = args.docling_enrich_picture_description
    docling_enrich_chart_extraction = args.docling_enrich_chart_extraction

    if args.quality == "fast":
        mineru_backend = mineru_backend or "pipeline"
        mineru_method = mineru_method or "txt"
        docling_table_mode = docling_table_mode or "fast"
    elif args.quality == "balanced":
        mineru_backend = mineru_backend or "pipeline"
        mineru_method = mineru_method or "txt"
        docling_table_mode = docling_table_mode or "accurate"
    elif args.quality == "best":
        mineru_backend = mineru_backend or "pipeline"
        mineru_method = mineru_method or "txt"
        docling_table_mode = docling_table_mode or "accurate"
        docling_image_export_mode = docling_image_export_mode or "referenced"

    if args.ocr:
        mineru_method = "ocr"
        docling_ocr = True
        docling_force_ocr = True

    if args.charts is True:
        mineru_image_analysis = True
        docling_image_export_mode = docling_image_export_mode or "referenced"
        docling_enrich_picture_description = True
        docling_enrich_chart_extraction = True
    elif args.charts is False:
        mineru_image_analysis = False
        docling_enrich_picture_description = False
        docling_enrich_chart_extraction = False

    return BackendOptions(
        mineru_backend=mineru_backend,
        mineru_method=mineru_method,
        mineru_lang=mineru_lang,
        mineru_start=args.start_page,
        mineru_end=args.end_page,
        mineru_formula=False if args.no_formula else None,
        mineru_table=False if args.no_table else None,
        mineru_image_analysis=mineru_image_analysis,
        mineru_api_url=args.mineru_api_url,
        mineru_server_url=args.mineru_server_url,
        docling_pipeline=args.docling_pipeline,
        docling_vlm_model=args.docling_vlm_model,
        docling_image_export_mode=docling_image_export_mode,
        docling_ocr=docling_ocr,
        docling_force_ocr=docling_force_ocr,
        docling_tables=args.docling_tables,
        docling_ocr_engine=args.docling_ocr_engine,
        docling_ocr_lang=docling_ocr_lang,
        docling_table_mode=docling_table_mode,
        docling_enrich_picture_classes=args.docling_enrich_picture_classes,
        docling_enrich_picture_description=docling_enrich_picture_description,
        docling_enrich_chart_extraction=docling_enrich_chart_extraction,
        docling_device=args.docling_device,
        docling_num_threads=args.docling_num_threads,
        remove_watermark=args.remove_watermark,
    )


def _write_result(target: Path, result: ConversionResult) -> None:
    result = relocate_resources(result, f"{target.stem}.assets")
    try:
        write_text(target, result.text)
        _copy_resources(target.parent, result)
    finally:
        _cleanup_result(result)


def _cleanup_result(result: ConversionResult) -> None:
    for path in result.cleanup_paths:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def _directory_output_path(input_file: Path, input_root: Path, output_root: Path | None) -> Path:
    if output_root is not None:
        return mirror_output_path(input_file, input_root, output_root)
    return default_output_path(input_file)


def _qualified_output_path(input_file: Path, input_root: Path, output_root: Path | None) -> Path:
    base = _directory_output_path(input_file, input_root, output_root)
    return base.with_name(f"{input_file.name}.md")


def _unique_output_path(preferred: Path, used: set[Path]) -> Path:
    if preferred not in used:
        return preferred
    for i in range(2, 1000):
        candidate = preferred.with_name(f"{preferred.stem}-{i}{preferred.suffix}")
        if candidate not in used:
            return candidate
    return preferred.with_name(f"{preferred.stem}-{id(preferred):x}{preferred.suffix}")


def _directory_output_paths(
    files: list[Path],
    input_root: Path,
    output_root: Path | None,
) -> dict[Path, Path]:
    base_targets = {f: _directory_output_path(f, input_root, output_root) for f in files}
    target_counts = Counter(base_targets.values())
    seen_base_targets: set[Path] = set()
    used_targets: set[Path] = set()
    targets: dict[Path, Path] = {}
    for f in files:
        base = base_targets[f]
        if target_counts[base] == 1 or base not in seen_base_targets:
            preferred = base
        else:
            preferred = _qualified_output_path(f, input_root, output_root)
        target = _unique_output_path(preferred, used_targets)
        targets[f] = target
        used_targets.add(target)
        seen_base_targets.add(base)
    return targets


def _copy_resources(target_dir: Path, result: ConversionResult) -> None:
    for resource in result.resources:
        resource_target = target_dir / resource.relative_path
        resource_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resource.source, resource_target)


def _selected_backend(args: argparse.Namespace) -> Backend:
    if args.backend != "auto":
        return args.backend
    if args.start_page is not None or args.end_page is not None:
        return "mineru"
    if args.quality == "fast":
        return "markitdown"
    if args.quality == "balanced":
        return "docling"
    return "mineru"


def _convert_directory(
    root: Path,
    out_dir: Path | None,
    recursive: bool,
    backend: Backend,
    verbose: bool,
    options: BackendOptions,
    skip_existing: bool = False,
) -> int:
    if not root.is_dir():
        print(f"x2md: not a directory: {root}", file=sys.stderr)
        return 2
    pattern = "**/*" if recursive else "*"
    files = sorted(p for p in root.glob(pattern) if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES)
    if not files:
        print(f"x2md: no supported files found in {root}", file=sys.stderr)
        return 1
    targets = _directory_output_paths(files, root, out_dir)
    errors = 0
    for f in files:
        target = targets[f]
        if skip_existing and target.exists():
            print(f"  {f}  ->  {target} (skipped, exists)", file=sys.stderr)
            continue
        try:
            result = convert_file_result(f, backend, verbose, options)
        except ConversionError as e:
            print(f"x2md: {e}", file=sys.stderr)
            errors += 1
            continue
        _write_result(target, result)
        print(f"  {f}  ->  {target}", file=sys.stderr)
    return 0 if errors == 0 else 1


def _convert_single(
    arg: str,
    output: str | None,
    fmt: str | None,
    backend: Backend,
    verbose: bool,
    options: BackendOptions,
) -> int:
    if arg == "-":
        if fmt is None:
            print("x2md: --format is required when reading from stdin", file=sys.stderr)
            return 2
        data = sys.stdin.buffer.read()
        result = convert_stream_result(data, fmt, backend, options, verbose)
        text = result.text
    elif is_url(arg):
        text = convert_url(arg, backend)
        result = ConversionResult(text)
    else:
        path = Path(arg).expanduser()
        result = convert_file_result(path, backend, verbose, options)
        text = result.text

    if output:
        _write_result(Path(output).expanduser(), result)
    elif arg != "-" and not is_url(arg) and output is None and sys.stdout.isatty():
        target = default_output_path(Path(arg).expanduser())
        _write_result(target, result)
        print(f"wrote {target}", file=sys.stderr)
    else:
        try:
            if result.resources:
                result = relocate_resources(result, "x2md.assets")
                text = result.text
                _copy_resources(Path.cwd(), result)
            write_stdout(text)
        finally:
            _cleanup_result(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _build_parser().parse_args(argv)

    if args.check_deps:
        missing = missing_deps()
        if missing:
            print(format_missing(missing))
        else:
            print("All optional external tools are present.")
        return 0

    if args.input is None:
        _build_parser().print_help()
        return 2

    if args.input == "web":
        from .webapp import run

        run(args.host, args.port)
        return 0

    try:
        options = _backend_options(args)
        backend = _selected_backend(args)
        input_path = Path(args.input).expanduser() if args.input != "-" and not is_url(args.input) else None
        if input_path is not None and input_path.is_dir():
            out_dir = Path(args.output_dir).expanduser() if args.output_dir else None
            return _convert_directory(
                input_path,
                out_dir,
                args.recursive,
                backend,
                not args.quiet,
                options,
                args.skip_existing,
            )
        return _convert_single(args.input, args.output, args.format, backend, not args.quiet, options)
    except ConversionError as e:
        print(f"x2md: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
