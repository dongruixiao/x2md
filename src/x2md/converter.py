"""Document conversion backends."""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import warnings
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path

Backend = str

BACKENDS = ("markitdown", "docling", "mineru", "rapiddoc", "auto")


class ConversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Resource:
    source: Path
    relative_path: Path


@dataclass(frozen=True)
class ConversionResult:
    text: str
    resources: tuple[Resource, ...] = ()
    cleanup_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class BackendOptions:
    mineru_backend: str | None = None
    mineru_method: str | None = None
    mineru_lang: str | None = None
    mineru_start: int | None = None
    mineru_end: int | None = None
    mineru_formula: bool | None = None
    mineru_table: bool | None = None
    mineru_image_analysis: bool | None = None
    mineru_api_url: str | None = None
    mineru_server_url: str | None = None
    docling_pipeline: str | None = None
    docling_vlm_model: str | None = None
    docling_image_export_mode: str | None = None
    docling_ocr: bool | None = None
    docling_force_ocr: bool | None = None
    docling_tables: bool | None = None
    docling_ocr_engine: str | None = None
    docling_ocr_lang: str | None = None
    docling_table_mode: str | None = None
    docling_enrich_picture_classes: bool = False
    docling_enrich_picture_description: bool = False
    docling_enrich_chart_extraction: bool = False
    docling_device: str | None = None
    docling_num_threads: int | None = None
    rapiddoc_lang: str | None = None
    rapiddoc_parse_method: str | None = None
    rapiddoc_start: int | None = None
    rapiddoc_end: int | None = None
    rapiddoc_formula: bool | None = None
    rapiddoc_table: bool | None = None
    remove_watermark: bool = False

    def has_docling_cli_options(self) -> bool:
        return any(
            value is not None
            for value in (
                self.docling_pipeline,
                self.docling_vlm_model,
                self.docling_image_export_mode,
                self.docling_ocr,
                self.docling_force_ocr,
                self.docling_tables,
                self.docling_ocr_engine,
                self.docling_ocr_lang,
                self.docling_table_mode,
                self.docling_device,
                self.docling_num_threads,
            )
        ) or any(
            (
                self.docling_enrich_picture_classes,
                self.docling_enrich_picture_description,
                self.docling_enrich_chart_extraction,
            )
        )


@lru_cache(maxsize=1)
def _md():
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Couldn't find ffmpeg.*", category=RuntimeWarning)
        try:
            from markitdown import MarkItDown
        except ImportError as e:
            raise ConversionError("markitdown backend is not installed") from e
    return MarkItDown()


@lru_cache(maxsize=1)
def _docling_converter():
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as e:
        raise ConversionError("docling backend is not installed; install with: pip install 'x2md[docling]'") from e
    return DocumentConverter()


def _normalize_backend(backend: Backend, path: Path | None = None) -> Backend:
    if backend not in BACKENDS:
        raise ConversionError(f"unsupported backend: {backend}")
    if backend != "auto":
        return backend
    if path is not None and path.suffix.lower() == ".pdf" and _find_executable("mineru") is not None:
        return "mineru"
    if path is not None and path.suffix.lower() == ".pdf" and find_spec("docling") is not None:
        return "docling"
    return "markitdown"


def _convert_file_markitdown(path: Path) -> str:
    try:
        result = _md().convert(str(path))
    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError(f"failed to convert {path.name}: {e}") from e
    return result.text_content or ""


def _convert_file_docling(path: Path) -> str:
    try:
        result = _docling_converter().convert(str(path))
        return result.document.export_to_markdown() or ""
    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError(f"failed to convert {path.name}: {e}") from e


def _write_rapiddoc_resources(images: dict[str, bytes]) -> tuple[tuple[Resource, ...], tuple[Path, ...]]:
    if not images:
        return (), ()
    output_dir = Path(tempfile.mkdtemp(prefix="x2md-rapiddoc-"))
    resources = []
    try:
        for image_path, image_bytes in images.items():
            relative_path = Path(image_path)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                relative_path = Path("images") / relative_path.name
            target = output_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(image_bytes)
            resources.append(Resource(target, relative_path))
        return tuple(resources), (output_dir,)
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise


@contextmanager
def _suppress_rapiddoc_logs():
    try:
        from loguru import logger
    except ImportError:
        logger = None

    if logger is not None:
        logger.remove()

    with open(os.devnull, "w", encoding="utf-8") as devnull:
        try:
            with redirect_stdout(devnull), redirect_stderr(devnull):
                yield
        finally:
            if logger is not None:
                logger.remove()
                logger.add(sys.stderr)


@lru_cache(maxsize=1)
def _rapiddoc_converter():
    try:
        from rapid_doc import RapidDoc
    except ImportError as e:
        raise ConversionError("rapid conversion engine is not installed; install with: pip install 'x2md[rapiddoc]'") from e
    return RapidDoc(image_output_mode="url", image_dir_name="images")


def _convert_file_rapiddoc_result(path: Path, options: BackendOptions) -> ConversionResult:
    try:
        with _suppress_rapiddoc_logs():
            result = _rapiddoc_converter()(
                path,
                lang=options.rapiddoc_lang or options.mineru_lang or "ch",
                parse_method=options.rapiddoc_parse_method or options.mineru_method or "auto",
                start_page_id=options.rapiddoc_start if options.rapiddoc_start is not None else options.mineru_start or 0,
                end_page_id=options.rapiddoc_end if options.rapiddoc_end is not None else options.mineru_end,
                formula_enable=options.rapiddoc_formula if options.rapiddoc_formula is not None else options.mineru_formula,
                table_enable=options.rapiddoc_table if options.rapiddoc_table is not None else options.mineru_table,
                f_dump_middle_json=False,
                f_dump_content_list=False,
            )
        text = getattr(result, "markdown", "") or ""
        images = getattr(result, "images", {}) or {}
        resources, cleanup_paths = _write_rapiddoc_resources(images)
        return ConversionResult(text=text, resources=resources, cleanup_paths=cleanup_paths)
    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError(f"failed to convert {path.name} with RapidDoc: {e}") from e


def _find_mineru_markdown(output_dir: Path) -> Path:
    candidates = [p for p in output_dir.rglob("*.md") if p.is_file()]
    if not candidates:
        raise ConversionError("mineru did not produce a Markdown file")
    return max(candidates, key=lambda p: (p.stat().st_size, p.stat().st_mtime))


def _find_markdown(output_dir: Path, tool: str) -> Path:
    candidates = [p for p in output_dir.rglob("*.md") if p.is_file()]
    if not candidates:
        raise ConversionError(f"{tool} did not produce a Markdown file")
    return max(candidates, key=lambda p: (p.stat().st_size, p.stat().st_mtime))


def _find_mineru_resources(md_path: Path) -> tuple[Resource, ...]:
    resources = []
    for images_dir in md_path.parent.rglob("images"):
        if not images_dir.is_dir():
            continue
        for image in images_dir.rglob("*"):
            if image.is_file():
                resources.append(Resource(image, image.relative_to(md_path.parent)))
    return tuple(resources)


def _find_output_resources(md_path: Path) -> tuple[Resource, ...]:
    return tuple(
        Resource(path, path.relative_to(md_path.parent))
        for path in md_path.parent.rglob("*")
        if path.is_file() and path != md_path
    )


def _rewrite_resource_links(text: str, resources: tuple[Resource, ...]) -> str:
    for resource in resources:
        text = text.replace(str(resource.source), resource.relative_path.as_posix())
    return text


def _markdown_path(path: Path) -> str:
    value = path.as_posix()
    return f"<{value}>" if any(char.isspace() or char in "()" for char in value) else value


def relocate_resources(result: ConversionResult, asset_dir_name: str) -> ConversionResult:
    resources = []
    text = result.text
    for resource in result.resources:
        old_path = resource.relative_path.as_posix()
        new_relative_path = Path(asset_dir_name) / resource.relative_path
        text = text.replace(old_path, _markdown_path(new_relative_path))
        resources.append(Resource(resource.source, new_relative_path))
    return ConversionResult(
        text=text,
        resources=tuple(resources),
        cleanup_paths=result.cleanup_paths,
    )


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _remove_pdf_watermark_artifacts(path: Path) -> Path | None:
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import NameObject
    except ImportError:
        return None

    try:
        reader = PdfReader(str(path))
        writer = PdfWriter()
        changed = False
        for page in reader.pages:
            annots = page.get("/Annots")
            if annots:
                kept_annots = []
                for annot in annots:
                    obj = annot.get_object()
                    if obj.get("/Subtype") in {"/Stamp", "/Watermark"}:
                        changed = True
                        continue
                    kept_annots.append(annot)
                if kept_annots:
                    page[NameObject("/Annots")] = kept_annots
                else:
                    del page[NameObject("/Annots")]
            writer.add_page(page)

        root = reader.trailer.get("/Root", {})
        if "/OCProperties" in root:
            writer._root_object.update({key: value for key, value in root.items() if key != "/OCProperties"})
            changed = True

        if not changed:
            return None

        with tempfile.NamedTemporaryFile(prefix="x2md-watermark-", suffix=".pdf", delete=False) as f:
            tmp_path = Path(f.name)
            writer.write(f)
        return tmp_path
    except Exception:
        return None


def _remove_watermark_text(text: str) -> str:
    dates = re.findall(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", text)
    repeated_dates = {date for date in set(dates) if dates.count(date) >= 3}
    if not repeated_dates:
        return text

    cleaned_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append(line)
            continue
        if any(date in stripped for date in repeated_dates) and len(stripped) >= 16:
            continue
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if text.endswith("\n"):
        cleaned += "\n"
    return cleaned


def _find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found is not None:
        return found
    sibling = Path(sys.executable).parent / name
    if sibling.exists():
        return str(sibling)
    suffixes = os.environ.get("PATHEXT", ".EXE;.BAT;.CMD").split(os.pathsep if os.pathsep in os.environ.get("PATHEXT", "") else ";")
    for suffix in suffixes:
        candidate = sibling.with_suffix(suffix.lower())
        if candidate.exists():
            return str(candidate)
        candidate = sibling.with_suffix(suffix.upper())
        if candidate.exists():
            return str(candidate)
    return None


def _run_command(cmd: list[str], tool: str, path: Path, verbose: bool) -> None:
    _run_command_with_label(cmd, tool, path, path, verbose)


def _tail_text(path: Path, max_chars: int = 1200) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)[-max_chars:]


def _temp_log_path(prefix: str) -> Path:
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".log")
    os.close(fd)
    return Path(name)


def _run_command_with_label(cmd: list[str], tool: str, path: Path, display_path: Path, verbose: bool) -> None:
    stdout_path = _temp_log_path("x2md-stdout-")
    stderr_path = _temp_log_path("x2md-stderr-")
    try:
        with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout, stderr_path.open(
            "w", encoding="utf-8", errors="replace"
        ) as stderr:
            if verbose:
                sys.stderr.write(f"Converting {display_path.name}")
                sys.stderr.flush()
                process = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, text=True)
                while process.poll() is None:
                    sys.stderr.write(".")
                    sys.stderr.flush()
                    time.sleep(1)
                sys.stderr.write(" done\n" if process.returncode == 0 else " failed\n")
                completed = subprocess.CompletedProcess(cmd, process.returncode)
            else:
                completed = subprocess.run(cmd, stdout=stdout, stderr=stderr, text=True, check=False)
    except OSError as e:
        raise ConversionError(f"conversion engine is unavailable: {e}") from e
    try:
        if completed.returncode != 0:
            detail = _tail_text(stderr_path) or _tail_text(stdout_path)
            message = f"failed to convert {display_path.name}"
            if detail:
                message = f"{message}: {detail}"
            raise ConversionError(message)
    finally:
        stdout_path.unlink(missing_ok=True)
        stderr_path.unlink(missing_ok=True)


def _build_mineru_cmd(mineru: str, path: Path, output_dir: Path, options: BackendOptions) -> list[str]:
    cmd = [mineru, "-p", str(path), "-o", str(output_dir), "-b", options.mineru_backend or "pipeline"]
    if options.mineru_method:
        cmd.extend(["-m", options.mineru_method])
    if options.mineru_lang:
        cmd.extend(["-l", options.mineru_lang])
    if options.mineru_start is not None:
        cmd.extend(["-s", str(options.mineru_start)])
    if options.mineru_end is not None:
        cmd.extend(["-e", str(options.mineru_end)])
    if options.mineru_formula is not None:
        cmd.extend(["-f", _bool_text(options.mineru_formula)])
    if options.mineru_table is not None:
        cmd.extend(["-t", _bool_text(options.mineru_table)])
    if options.mineru_image_analysis is not None:
        cmd.extend(["--image-analysis", _bool_text(options.mineru_image_analysis)])
    if options.mineru_api_url:
        cmd.extend(["--api-url", options.mineru_api_url])
    if options.mineru_server_url:
        cmd.extend(["-u", options.mineru_server_url])
    return cmd


def _build_docling_cmd(docling: str, path: Path, output_dir: Path, options: BackendOptions) -> list[str]:
    cmd = [
        docling,
        str(path),
        "--to",
        "md",
        "--output",
        str(output_dir),
        "--image-export-mode",
        options.docling_image_export_mode or "referenced",
    ]
    if options.docling_pipeline:
        cmd.extend(["--pipeline", options.docling_pipeline])
    if options.docling_vlm_model:
        cmd.extend(["--vlm-model", options.docling_vlm_model])
    if options.docling_ocr is not None:
        cmd.append("--ocr" if options.docling_ocr else "--no-ocr")
    if options.docling_force_ocr is not None:
        cmd.append("--force-ocr" if options.docling_force_ocr else "--no-force-ocr")
    if options.docling_tables is not None:
        cmd.append("--tables" if options.docling_tables else "--no-tables")
    if options.docling_ocr_engine:
        cmd.extend(["--ocr-engine", options.docling_ocr_engine])
    if options.docling_ocr_lang:
        cmd.extend(["--ocr-lang", options.docling_ocr_lang])
    if options.docling_table_mode:
        cmd.extend(["--table-mode", options.docling_table_mode])
    if options.docling_enrich_picture_classes:
        cmd.append("--enrich-picture-classes")
    if options.docling_enrich_picture_description:
        cmd.append("--enrich-picture-description")
    if options.docling_enrich_chart_extraction:
        cmd.append("--enrich-chart-extraction")
    if options.docling_device:
        cmd.extend(["--device", options.docling_device])
    if options.docling_num_threads is not None:
        cmd.extend(["--num-threads", str(options.docling_num_threads)])
    return cmd


def _convert_file_docling_cli_result(
    path: Path,
    options: BackendOptions,
    verbose: bool,
    display_path: Path | None = None,
) -> ConversionResult:
    docling = _find_executable("docling")
    if docling is None:
        raise ConversionError("balanced conversion engine is not installed; reinstall x2md")

    output_dir = Path(tempfile.mkdtemp(prefix="x2md-docling-"))
    try:
        cmd = _build_docling_cmd(docling, path, output_dir, options)
        _run_command_with_label(cmd, "docling", path, display_path or path, verbose)
        md_path = _find_markdown(output_dir, "docling")
        text = md_path.read_text(encoding="utf-8")
        resources = _find_output_resources(md_path)
        text = _rewrite_resource_links(text, resources)
        return ConversionResult(text=text, resources=resources, cleanup_paths=(output_dir,))
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise


def _convert_file_mineru_result(
    path: Path,
    options: BackendOptions,
    verbose: bool = True,
    display_path: Path | None = None,
) -> ConversionResult:
    mineru = _find_executable("mineru")
    if mineru is None:
        raise ConversionError("best-quality conversion engine is not installed; reinstall x2md")

    output_dir = Path(tempfile.mkdtemp(prefix="x2md-mineru-"))
    try:
        cmd = _build_mineru_cmd(mineru, path, output_dir, options)
        _run_command_with_label(cmd, "mineru", path, display_path or path, verbose)
        md_path = _find_mineru_markdown(output_dir)
        text = md_path.read_text(encoding="utf-8")
        resources = _find_mineru_resources(md_path)
        return ConversionResult(text=text, resources=resources, cleanup_paths=(output_dir,))
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise


def convert_file_result(
    path: Path,
    backend: Backend = "markitdown",
    verbose: bool = True,
    options: BackendOptions | None = None,
) -> ConversionResult:
    options = options or BackendOptions()
    if not path.exists():
        raise ConversionError(f"file not found: {path}")
    if not path.is_file():
        raise ConversionError(f"not a file: {path}")
    display_path = path
    cleanup_paths: tuple[Path, ...] = ()
    if options.remove_watermark and path.suffix.lower() == ".pdf":
        cleaned_path = _remove_pdf_watermark_artifacts(path)
        if cleaned_path is not None:
            cleanup_paths = (cleaned_path,)
            path = cleaned_path

    def convert_input(input_path: Path) -> ConversionResult:
        selected_backend = _normalize_backend(backend, input_path)
        if selected_backend == "docling":
            if options.has_docling_cli_options():
                return _convert_file_docling_cli_result(input_path, options, verbose, display_path)
            return ConversionResult(_convert_file_docling(input_path))
        if selected_backend == "mineru":
            return _convert_file_mineru_result(input_path, options, verbose, display_path)
        if selected_backend == "rapiddoc":
            return _convert_file_rapiddoc_result(input_path, options)
        return ConversionResult(_convert_file_markitdown(input_path))

    try:
        result = convert_input(path)
    except ConversionError:
        if path == display_path:
            raise
        result = convert_input(display_path)

    text = _remove_watermark_text(result.text) if options.remove_watermark else result.text
    return ConversionResult(
        text=text,
        resources=result.resources,
        cleanup_paths=result.cleanup_paths + cleanup_paths,
    )


def convert_file(path: Path, backend: Backend = "markitdown") -> str:
    return convert_file_result(path, backend).text


def convert_url(url: str, backend: Backend = "markitdown") -> str:
    backend = _normalize_backend(backend)
    if backend in {"mineru", "rapiddoc"}:
        raise ConversionError(f"{backend} backend does not support URL input")
    try:
        if backend == "docling":
            result = _docling_converter().convert(url)
            return result.document.export_to_markdown() or ""
        result = _md().convert(url)
    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError(f"failed to convert URL {url}: {e}") from e
    return result.text_content or ""


def convert_stream_result(
    data: bytes,
    suffix: str,
    backend: Backend = "markitdown",
    options: BackendOptions | None = None,
    verbose: bool = True,
) -> ConversionResult:
    if not suffix.startswith("."):
        suffix = "." + suffix
    backend = _normalize_backend(backend, Path("stdin").with_suffix(suffix))
    if backend in {"docling", "mineru", "rapiddoc"}:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(data)
            tmp_path = Path(f.name)
        try:
            result = convert_file_result(tmp_path, backend, verbose, options)
            return ConversionResult(
                text=result.text,
                resources=result.resources,
                cleanup_paths=result.cleanup_paths + (tmp_path,),
            )
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    try:
        result = _md().convert_stream(io.BytesIO(data), file_extension=suffix)
    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError(f"failed to convert stdin ({suffix}): {e}") from e
    return ConversionResult(result.text_content or "")


def convert_stream(data: bytes, suffix: str, backend: Backend = "markitdown") -> str:
    result = convert_stream_result(data, suffix, backend)
    try:
        return result.text
    finally:
        for path in result.cleanup_paths:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
