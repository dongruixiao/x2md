import io
import sys
import types
from pathlib import Path

import pytest

from x2md import cli
from x2md.converter import (
    BackendOptions,
    ConversionError,
    ConversionResult,
    Resource,
    _build_docling_cmd,
    _build_mineru_cmd,
    _find_executable,
    _normalize_markdown_line_breaks,
    _run_command_with_label,
    _remove_watermark_text,
    _rewrite_resource_links,
    convert_file,
    convert_file_result,
    relocate_resources,
)
from x2md.io_utils import default_output_path, is_url, mirror_output_path
from x2md.webapp import create_app


def test_is_url():
    assert is_url("https://example.com")
    assert is_url("http://example.com")
    assert not is_url("./file.pdf")
    assert not is_url("C:\\docs\\file.pdf")


def test_default_output_path():
    assert default_output_path(Path("a/b/c.pdf")) == Path("a/b/c.md")


def test_mirror_output_path():
    src = Path("/in/sub/file.docx")
    out = mirror_output_path(src, Path("/in"), Path("/out"))
    assert out == Path("/out/sub/file.md")


def test_cli_passes_backend_to_converter(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    out = tmp_path / "out.md"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append((path, backend, verbose, options))
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(src), "-o", str(out), "--backend", "docling"]) == 0
    assert calls == [(src, "docling", True, BackendOptions(
        mineru_backend="pipeline",
        mineru_method="txt",
        mineru_lang="ch",
        docling_image_export_mode="referenced",
        docling_ocr_lang="ch",
        docling_table_mode="accurate",
    ))]
    assert out.read_text(encoding="utf-8") == "# converted"


def test_cli_directory_skips_existing_by_default(tmp_path, monkeypatch):
    root = tmp_path / "input"
    out_dir = tmp_path / "out"
    root.mkdir()
    out_dir.mkdir()
    old_src = root / "old.pdf"
    new_src = root / "new.pdf"
    old_src.write_text("old pdf", encoding="utf-8")
    new_src.write_text("new pdf", encoding="utf-8")
    (out_dir / "old.md").write_text("# existing", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append(path)
        return ConversionResult(f"# converted {path.stem}")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(root), "-O", str(out_dir), "--backend", "markitdown"]) == 0
    assert calls == [new_src]
    assert (out_dir / "old.md").read_text(encoding="utf-8") == "# existing"
    assert (out_dir / "new.md").read_text(encoding="utf-8") == "# converted new"


def test_cli_directory_overwrite_reconverts_existing_outputs(tmp_path, monkeypatch):
    root = tmp_path / "input"
    out_dir = tmp_path / "out"
    root.mkdir()
    out_dir.mkdir()
    src = root / "old.pdf"
    src.write_text("old pdf", encoding="utf-8")
    (out_dir / "old.md").write_text("# existing", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append(path)
        return ConversionResult("# overwritten")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(root), "-O", str(out_dir), "--overwrite", "--backend", "markitdown"]) == 0
    assert calls == [src]
    assert (out_dir / "old.md").read_text(encoding="utf-8") == "# overwritten"


def test_cli_directory_skip_existing_flag_remains_compatible(tmp_path, monkeypatch):
    root = tmp_path / "input"
    out_dir = tmp_path / "out"
    root.mkdir()
    out_dir.mkdir()
    src = root / "old.pdf"
    src.write_text("old pdf", encoding="utf-8")
    (out_dir / "old.md").write_text("# existing", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append(path)
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(root), "-O", str(out_dir), "--skip-existing", "--backend", "markitdown"]) == 0
    assert calls == []


def test_cli_directory_disambiguates_same_stem_outputs(tmp_path, monkeypatch):
    root = tmp_path / "input"
    out_dir = tmp_path / "out"
    root.mkdir()
    out_dir.mkdir()
    docx = root / "report.docx"
    pdf = root / "report.pdf"
    docx.write_text("docx", encoding="utf-8")
    pdf.write_text("pdf", encoding="utf-8")

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        return ConversionResult(f"# converted {path.name}")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(root), "-O", str(out_dir), "--backend", "markitdown"]) == 0
    assert (out_dir / "report.md").read_text(encoding="utf-8") == "# converted report.docx"
    assert (out_dir / "report.pdf.md").read_text(encoding="utf-8") == "# converted report.pdf"


def test_cli_directory_default_skip_with_same_stem_converts_unseen_format(tmp_path, monkeypatch):
    root = tmp_path / "input"
    out_dir = tmp_path / "out"
    root.mkdir()
    out_dir.mkdir()
    docx = root / "report.docx"
    pdf = root / "report.pdf"
    docx.write_text("docx", encoding="utf-8")
    pdf.write_text("pdf", encoding="utf-8")
    (out_dir / "report.md").write_text("# existing docx", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append(path)
        return ConversionResult(f"# converted {path.name}")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(root), "-O", str(out_dir), "--backend", "markitdown"]) == 0
    assert calls == [pdf]
    assert (out_dir / "report.md").read_text(encoding="utf-8") == "# existing docx"
    assert (out_dir / "report.pdf.md").read_text(encoding="utf-8") == "# converted report.pdf"


def test_cli_quiet_disables_backend_logs(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    out = tmp_path / "out.md"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append((path, backend, verbose, options))
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(src), "-o", str(out), "--backend", "mineru", "--quiet"]) == 0
    assert calls == [(src, "mineru", False, BackendOptions(
        mineru_backend="pipeline",
        mineru_method="txt",
        mineru_lang="ch",
        docling_image_export_mode="referenced",
        docling_ocr_lang="ch",
        docling_table_mode="accurate",
    ))]


def test_cli_quality_fast_uses_fast_backend(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    out = tmp_path / "out.md"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append((backend, options))
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(src), "-o", str(out), "--quality", "fast"]) == 0
    assert calls == [("markitdown", BackendOptions(
        mineru_backend="pipeline",
        mineru_method="txt",
        mineru_lang="ch",
        docling_ocr_lang="ch",
        docling_table_mode="fast",
    ))]


def test_cli_quality_balanced_uses_docling(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    out = tmp_path / "out.md"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append((backend, options))
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(src), "-o", str(out), "--quality", "balanced"]) == 0
    assert calls == [("docling", BackendOptions(
        mineru_backend="pipeline",
        mineru_method="txt",
        mineru_lang="ch",
        docling_ocr_lang="ch",
        docling_table_mode="accurate",
    ))]


def test_cli_quality_best_uses_mineru(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    out = tmp_path / "out.md"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append((backend, options))
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(src), "-o", str(out), "--quality", "best"]) == 0
    assert calls == [("mineru", BackendOptions(
        mineru_backend="pipeline",
        mineru_method="txt",
        mineru_lang="ch",
        docling_image_export_mode="referenced",
        docling_ocr_lang="ch",
        docling_table_mode="accurate",
    ))]


def test_cli_quality_rapid_uses_rapiddoc(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    out = tmp_path / "out.md"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append((backend, options))
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(src), "-o", str(out), "--quality", "rapid"]) == 0
    assert calls == [("rapiddoc", BackendOptions(
        mineru_backend="pipeline",
        mineru_method="txt",
        mineru_lang="ch",
        docling_ocr_lang="ch",
        docling_table_mode="accurate",
        rapiddoc_lang="ch",
        rapiddoc_parse_method="txt",
        rapiddoc_formula=False,
    ))]


def test_cli_rapiddoc_backend_without_rapid_quality_keeps_formula_default(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    out = tmp_path / "out.md"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append((backend, options))
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(src), "-o", str(out), "--backend", "rapiddoc", "--quality", "balanced"]) == 0
    assert calls == [("rapiddoc", BackendOptions(
        mineru_backend="pipeline",
        mineru_method="txt",
        mineru_lang="ch",
        docling_ocr_lang="ch",
        docling_table_mode="accurate",
        rapiddoc_lang="ch",
        rapiddoc_parse_method="txt",
    ))]


def test_cli_page_range_uses_range_capable_backend(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    out = tmp_path / "out.md"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append((backend, options))
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(src), "-o", str(out), "--start-page", "0", "--end-page", "2"]) == 0
    assert calls == [("mineru", BackendOptions(
        mineru_backend="pipeline",
        mineru_method="txt",
        mineru_lang="ch",
        mineru_start=0,
        mineru_end=2,
        docling_image_export_mode="referenced",
        docling_ocr_lang="ch",
        docling_table_mode="accurate",
    ))]


def test_cli_ocr_flag_forces_ocr(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    out = tmp_path / "out.md"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append((backend, options))
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(src), "-o", str(out), "--ocr"]) == 0
    assert calls == [("mineru", BackendOptions(
        mineru_backend="pipeline",
        mineru_method="ocr",
        mineru_lang="ch",
        docling_image_export_mode="referenced",
        docling_ocr=True,
        docling_force_ocr=True,
        docling_ocr_lang="ch",
        docling_table_mode="accurate",
    ))]


def test_cli_no_table_applies_to_all_table_capable_backends(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    out = tmp_path / "out.md"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append((backend, options))
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(src), "-o", str(out), "--quality", "rapid", "--no-table"]) == 0
    assert calls == [("rapiddoc", BackendOptions(
        mineru_backend="pipeline",
        mineru_method="txt",
        mineru_lang="ch",
        mineru_table=False,
        docling_tables=False,
        docling_ocr_lang="ch",
        docling_table_mode="accurate",
        rapiddoc_lang="ch",
        rapiddoc_parse_method="txt",
        rapiddoc_formula=False,
        rapiddoc_table=False,
    ))]


def test_cli_remove_watermark_flag(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    out = tmp_path / "out.md"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append(options)
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([str(src), "-o", str(out), "--remove-watermark"]) == 0
    assert calls[0].remove_watermark is True


def test_cli_web_starts_server(monkeypatch):
    calls = []

    def fake_run(host, port, desktop=False):
        calls.append((host, port, desktop))

    monkeypatch.setattr("x2md.webapp.run", fake_run)

    assert cli.main(["web", "--host", "127.0.0.1", "--port", "9999"]) == 0
    assert calls == [("127.0.0.1", 9999, False)]


def test_cli_desktop_starts_tokenized_random_port_server(monkeypatch):
    calls = []

    def fake_run(host, port, desktop=False):
        calls.append((host, port, desktop))

    monkeypatch.setattr("x2md.webapp.run", fake_run)

    assert cli.main(["desktop"]) == 0
    assert calls == [("127.0.0.1", 0, True)]


def test_cli_builds_backend_options(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    out = tmp_path / "out.md"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append(options)
        return ConversionResult("# converted")

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)

    assert cli.main([
        str(src),
        "-o",
        str(out),
        "--backend",
        "mineru",
        "--mineru-backend",
        "hybrid-auto-engine",
        "--mineru-method",
        "ocr",
        "--mineru-lang",
        "ch",
        "--start-page",
        "1",
        "--end-page",
        "3",
        "--no-image-analysis",
    ]) == 0
    assert calls == [
        BackendOptions(
            mineru_backend="hybrid-auto-engine",
            mineru_method="ocr",
            mineru_lang="ch",
            mineru_start=1,
            mineru_end=3,
            mineru_image_analysis=False,
            docling_image_export_mode="referenced",
            docling_ocr_lang="ch",
            docling_table_mode="accurate",
        )
    ]


def test_cli_copies_conversion_resources(tmp_path):
    resource = tmp_path / "mineru-out" / "images" / "chart.jpg"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(b"jpg")
    out = tmp_path / "out.md"

    cli._write_result(out, ConversionResult("![chart](images/chart.jpg)", (Resource(resource, Path("images/chart.jpg")),)))

    assert out.read_text(encoding="utf-8") == "![chart](out.assets/images/chart.jpg)"
    assert (tmp_path / "out.assets" / "images" / "chart.jpg").read_bytes() == b"jpg"


def test_cli_copies_stdout_resources_to_current_directory(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    src.write_text("pdf", encoding="utf-8")
    resource = tmp_path / "mineru-out" / "images" / "chart.jpg"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(b"jpg")
    stdout = io.StringIO()

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        return ConversionResult(
            "![chart](images/chart.jpg)",
            (Resource(resource, Path("images/chart.jpg")),),
        )

    monkeypatch.setattr(cli, "convert_file_result", fake_convert_file_result)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.chdir(tmp_path)

    assert cli._convert_single(str(src), None, None, "mineru", True, BackendOptions()) == 0
    assert stdout.getvalue() == "![chart](x2md.assets/images/chart.jpg)\n"
    assert (tmp_path / "x2md.assets" / "images" / "chart.jpg").read_bytes() == b"jpg"


def test_cli_cleans_conversion_temp_paths(tmp_path):
    cleanup_dir = tmp_path / "mineru-temp"
    cleanup_dir.mkdir()
    out = tmp_path / "out.md"

    cli._write_result(out, ConversionResult("# converted", cleanup_paths=(cleanup_dir,)))

    assert out.read_text(encoding="utf-8") == "# converted"
    assert not cleanup_dir.exists()


def test_cli_applies_options_to_stdin_conversion(tmp_path, monkeypatch):
    out = tmp_path / "out.md"
    calls = []

    class FakeStdin:
        buffer = io.BytesIO(b"pdf")

    def fake_convert_stream_result(data, suffix, backend="markitdown", options=None, verbose=True):
        calls.append((data, suffix, backend, options, verbose))
        return ConversionResult("# converted")

    monkeypatch.setattr(cli.sys, "stdin", FakeStdin())
    monkeypatch.setattr(cli, "convert_stream_result", fake_convert_stream_result)

    assert cli.main(["-", "-f", "pdf", "-o", str(out), "--quality", "best", "--ocr", "--language", "en"]) == 0
    assert calls == [(
        b"pdf",
        "pdf",
        "mineru",
        BackendOptions(
            mineru_backend="pipeline",
            mineru_method="ocr",
            mineru_lang="en",
            docling_image_export_mode="referenced",
            docling_ocr=True,
            docling_force_ocr=True,
            docling_ocr_lang="en",
            docling_table_mode="accurate",
        ),
        True,
    )]
    assert out.read_text(encoding="utf-8") == "# converted"


def test_rewrite_resource_links_uses_relative_paths(tmp_path):
    source = tmp_path / "out" / "doc_artifacts" / "image.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png")
    text = f"![Image]({source})"

    rewritten = _rewrite_resource_links(text, (Resource(source, Path("doc_artifacts/image.png")),))

    assert rewritten == "![Image](doc_artifacts/image.png)"


def test_relocate_resources_uses_per_document_asset_directory(tmp_path):
    source = tmp_path / "out" / "images" / "chart.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png")
    result = ConversionResult(
        "![chart](images/chart.png)",
        (Resource(source, Path("images/chart.png")),),
    )

    relocated = relocate_resources(result, "report.assets")

    assert relocated.text == "![chart](report.assets/images/chart.png)"
    assert relocated.resources == (Resource(source, Path("report.assets/images/chart.png")),)


def test_relocate_resources_wraps_paths_with_spaces_for_markdown(tmp_path):
    source = tmp_path / "out" / "images" / "chart.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png")
    result = ConversionResult(
        "![chart](images/chart.png)",
        (Resource(source, Path("images/chart.png")),),
    )

    relocated = relocate_resources(result, "MaaS 平台 upstream benchmark 测试规范 v1.0-2.assets")

    assert relocated.text == "![chart](<MaaS 平台 upstream benchmark 测试规范 v1.0-2.assets/images/chart.png>)"


def test_remove_watermark_text_drops_repeated_date_lines():
    text = (
        "正文第一段\n"
        "国网上海市电力公司 党委党建部 赵亮 2025-06-20\n"
        "正文第二段\n"
        "上海市电力公司 党委党建部 赵亮 2025-06-20\n"
        "正文第三段\n"
        "党委党建部 赵亮 2025-06-20\n"
    )

    cleaned = _remove_watermark_text(text)

    assert "正文第一段" in cleaned
    assert "正文第二段" in cleaned
    assert "正文第三段" in cleaned
    assert "赵亮 2025-06-20" not in cleaned


def test_normalize_markdown_line_breaks_joins_cjk_pdf_wraps():
    text = (
        "上海市经济信息化委关于同意进一步开展上海市电力\n"
        "需求响应和虚拟电厂工作的批复\n"
        "\n"
        "国网上海市电力公司：\n"
        "\n"
        "《国网上海市电力公司关于进一步深化电力需求响应和虚拟\n"
        "\n"
        "电厂工作的请示》（国网上电司销„2020‟510 号）收悉。根据《中\n"
        "\n"
        "共中央、国务院关于进一步深化电力体制改革的若干意见》，\n"
        "\n"
        "经研究，批复如下：\n"
        "\n"
        "一、同意你公司关于进一步深化电力需求响应和虚拟电厂工\n"
        "\n"
        "作的请示。\n"
        "\n"
        "— 1 —\n"
    )

    assert _normalize_markdown_line_breaks(text) == (
        "上海市经济信息化委关于同意进一步开展上海市电力需求响应和虚拟电厂工作的批复\n"
        "\n"
        "国网上海市电力公司：\n"
        "\n"
        "《国网上海市电力公司关于进一步深化电力需求响应和虚拟电厂工作的请示》（国网上电司销„2020‟510 号）收悉。根据《中共中央、国务院关于进一步深化电力体制改革的若干意见》，经研究，批复如下：\n"
        "\n"
        "一、同意你公司关于进一步深化电力需求响应和虚拟电厂工作的请示。\n"
    )


def test_web_app_serves_index():
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    response = client.get("/")

    assert response.status_code == 200
    assert "x2md 文档转换工具" in response.text
    assert "canStartFreshQueue" in response.text
    assert 'value="rapid"' in response.text
    assert "覆盖已有输出" in response.text


def test_web_app_api_token_protects_desktop_api():
    from fastapi.testclient import TestClient

    client = TestClient(create_app("secret"))

    assert client.get("/").status_code == 200
    assert client.get("/api/jobs/missing").status_code == 403
    assert client.get("/api/jobs/missing", headers={"x-x2md-token": "secret"}).status_code == 404


def test_web_app_desktop_diagnostics_hidden_without_desktop_token():
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    index = client.get("/")

    assert index.status_code == 200
    assert 'id="desktopDiagnostics" type="button" hidden' in index.text
    diagnostics = client.get("/api/desktop-diagnostics").json()
    assert diagnostics["desktop"] is False


def test_web_app_desktop_diagnostics_reports_runtime(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("X2MD_DESKTOP_RUNTIME_SOURCE", "bundled")
    monkeypatch.setenv("X2MD_DESKTOP_RUNTIME_PYTHON", "/runtime/python")
    monkeypatch.setenv("X2MD_MODEL_CACHE", "/cache/models")
    monkeypatch.setenv("X2MD_DESKTOP_LIGHT", "1")
    client = TestClient(create_app("secret"))

    index = client.get("/")
    diagnostics = client.get("/api/desktop-diagnostics", headers={"x-x2md-token": "secret"}).json()

    assert index.status_code == 200
    assert "桌面诊断" in index.text
    assert diagnostics["desktop"] is True
    assert diagnostics["runtime_source"] == "bundled"
    assert diagnostics["runtime_python"] == "/runtime/python"
    assert diagnostics["model_cache"] == "/cache/models"
    assert diagnostics["light_runtime"] is True


def test_web_app_select_output_dir_returns_selected_path(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr("x2md.webapp._select_folder", lambda: "/tmp/x2md-out")
    client = TestClient(create_app("secret"))

    response = client.post("/api/select-output-dir", headers={"x-x2md-token": "secret"})

    assert response.status_code == 200
    assert response.json() == {"path": "/tmp/x2md-out"}


def test_web_job_converts_uploaded_file(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        assert backend == "markitdown"
        assert options.remove_watermark is True
        resource = tmp_path / "source" / "images" / "chart.jpg"
        resource.parent.mkdir(parents=True, exist_ok=True)
        resource.write_bytes(b"jpg")
        return ConversionResult(
            "# converted\n\n![chart](images/chart.jpg)",
            (Resource(resource, Path("images/chart.jpg")),),
        )

    monkeypatch.setattr("x2md.webapp.convert_file_result", fake_convert_file_result)
    client = TestClient(create_app())

    response = client.post(
        "/api/jobs",
        data={
            "output_dir": str(tmp_path),
            "quality": "fast",
            "remove_watermark": "true",
        },
        files=[("files", ("sample.pdf", b"pdf", "application/pdf"))],
    )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    for _ in range(20):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "done":
            break
    assert job["status"] == "done"
    assert job["files"][0]["download_url"]
    assert (tmp_path / "sample.md").read_text(encoding="utf-8") == "# converted\n\n![chart](sample.assets/images/chart.jpg)"
    assert (tmp_path / "sample.assets" / "images" / "chart.jpg").read_bytes() == b"jpg"


def test_web_job_skips_existing_output_by_default(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    existing = tmp_path / "sample.md"
    existing.write_text("# existing", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append(path)
        return ConversionResult("# converted")

    monkeypatch.setattr("x2md.webapp.convert_file_result", fake_convert_file_result)
    client = TestClient(create_app())

    response = client.post(
        "/api/jobs",
        data={"output_dir": str(tmp_path), "quality": "fast"},
        files=[("files", ("sample.pdf", b"pdf", "application/pdf"))],
    )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    for _ in range(20):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "done":
            break
    assert calls == []
    assert job["files"][0]["message"] == "Skipped existing output"
    assert existing.read_text(encoding="utf-8") == "# existing"


def test_web_job_overwrite_reconverts_existing_output(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    existing = tmp_path / "sample.md"
    existing.write_text("# existing", encoding="utf-8")
    calls = []

    def fake_convert_file_result(path, backend="markitdown", verbose=True, options=None):
        calls.append(path)
        return ConversionResult("# converted")

    monkeypatch.setattr("x2md.webapp.convert_file_result", fake_convert_file_result)
    client = TestClient(create_app())

    response = client.post(
        "/api/jobs",
        data={"output_dir": str(tmp_path), "quality": "fast", "overwrite": "true"},
        files=[("files", ("sample.pdf", b"pdf", "application/pdf"))],
    )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    for _ in range(20):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "done":
            break
    assert calls
    assert existing.read_text(encoding="utf-8") == "# converted"


def test_mineru_backend_requires_cli(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    src.write_text("pdf", encoding="utf-8")
    monkeypatch.setattr("x2md.converter._find_executable", lambda _: None)

    with pytest.raises(ConversionError, match="best-quality conversion engine is not installed"):
        convert_file(src, "mineru")


def test_light_desktop_runtime_explains_missing_mineru(tmp_path, monkeypatch):
    src = tmp_path / "input.pdf"
    src.write_text("pdf", encoding="utf-8")
    monkeypatch.setenv("X2MD_DESKTOP_LIGHT", "1")
    monkeypatch.setattr("x2md.converter._find_executable", lambda _: None)

    with pytest.raises(ConversionError, match="lightweight desktop runtime"):
        convert_file(src, "mineru")


def test_rapiddoc_backend_uses_python_api_copies_images_and_shows_progress(tmp_path, monkeypatch, capsys):
    src = tmp_path / "input.pdf"
    src.write_text("pdf", encoding="utf-8")
    calls = []

    class FakeRapidDoc:
        def __init__(self, **kwargs):
            print("rapid init log")
            calls.append(("init", kwargs))

        def __call__(self, path, **kwargs):
            print("rapid call log")
            print("rapid err log", file=sys.stderr)
            calls.append(("call", path, kwargs))
            return types.SimpleNamespace(
                markdown="![chart](images/chart.png)",
                images={"images/chart.png": b"png"},
            )

    monkeypatch.setitem(sys.modules, "rapid_doc", types.SimpleNamespace(RapidDoc=FakeRapidDoc))
    from x2md import converter

    converter._rapiddoc_converter.cache_clear()
    result = convert_file_result(
        src,
        "rapiddoc",
        options=BackendOptions(
            rapiddoc_lang="en",
            rapiddoc_parse_method="ocr",
            rapiddoc_start=1,
            rapiddoc_end=2,
            rapiddoc_formula=False,
            rapiddoc_table=False,
        ),
    )

    try:
        assert result.text == "![chart](images/chart.png)"
        assert len(result.resources) == 1
        assert result.resources[0].relative_path == Path("images/chart.png")
        assert result.resources[0].source.read_bytes() == b"png"
        assert calls[0] == ("init", {"image_output_mode": "url", "image_dir_name": "images"})
        assert calls[1] == ("call", src, {
            "lang": "en",
            "parse_method": "ocr",
            "start_page_id": 1,
            "end_page_id": 2,
            "formula_enable": False,
            "table_enable": False,
            "f_dump_middle_json": False,
            "f_dump_content_list": False,
        })
        captured = capsys.readouterr()
        assert "rapid init log" not in captured.out
        assert "rapid call log" not in captured.out
        assert "rapid err log" not in captured.err
        assert f"Converting {src.name}" in captured.err
        assert " done\n" in captured.err
    finally:
        for path in result.cleanup_paths:
            if path.is_dir():
                import shutil
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        converter._rapiddoc_converter.cache_clear()


def test_rapiddoc_backend_quiet_hides_progress(tmp_path, monkeypatch, capsys):
    src = tmp_path / "input.pdf"
    src.write_text("pdf", encoding="utf-8")

    class FakeRapidDoc:
        def __init__(self, **kwargs):
            pass

        def __call__(self, path, **kwargs):
            return types.SimpleNamespace(markdown="# converted", images={})

    monkeypatch.setitem(sys.modules, "rapid_doc", types.SimpleNamespace(RapidDoc=FakeRapidDoc))
    from x2md import converter

    converter._rapiddoc_converter.cache_clear()
    try:
        result = convert_file_result(src, "rapiddoc", verbose=False)
        assert result.text == "# converted"
        captured = capsys.readouterr()
        assert captured.err == ""
    finally:
        converter._rapiddoc_converter.cache_clear()


def test_find_executable_checks_windows_suffixes(tmp_path, monkeypatch):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    python_exe = scripts / "python.exe"
    python_exe.write_text("", encoding="utf-8")
    tool = scripts / "mineru.exe"
    tool.write_text("", encoding="utf-8")

    monkeypatch.setattr("x2md.converter.shutil.which", lambda _: None)
    monkeypatch.setattr("x2md.converter.sys.executable", str(python_exe))
    monkeypatch.setenv("PATHEXT", ".EXE;.BAT;.CMD")

    assert _find_executable("mineru") == str(tool)


def test_build_mineru_cmd_includes_useful_options(tmp_path):
    cmd = _build_mineru_cmd(
        "mineru",
        tmp_path / "input.pdf",
        tmp_path / "out",
        BackendOptions(
            mineru_backend="hybrid-auto-engine",
            mineru_method="ocr",
            mineru_lang="ch",
            mineru_start=2,
            mineru_end=5,
            mineru_formula=False,
            mineru_table=False,
            mineru_image_analysis=False,
            mineru_api_url="http://api",
            mineru_server_url="http://server",
        ),
    )

    assert cmd == [
        "mineru",
        "-p",
        str(tmp_path / "input.pdf"),
        "-o",
        str(tmp_path / "out"),
        "-b",
        "hybrid-auto-engine",
        "-m",
        "ocr",
        "-l",
        "ch",
        "-s",
        "2",
        "-e",
        "5",
        "-f",
        "false",
        "-t",
        "false",
        "--image-analysis",
        "false",
        "--api-url",
        "http://api",
        "-u",
        "http://server",
    ]


def test_run_command_progress_uses_display_name(tmp_path, monkeypatch):
    messages = []

    class FakeStderr:
        def write(self, value):
            messages.append(value)

        def flush(self):
            pass

    class FakeProcess:
        returncode = 0

        def __init__(self):
            self._polls = 0

        def poll(self):
            self._polls += 1
            return None if self._polls == 1 else 0

    monkeypatch.setattr("x2md.converter.sys.stderr", FakeStderr())
    monkeypatch.setattr("x2md.converter.subprocess.Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr("x2md.converter.time.sleep", lambda seconds: None)

    _run_command_with_label(
        ["tool"],
        "tool",
        tmp_path / "x2md-watermark-abc.pdf",
        tmp_path / "original.pdf",
        True,
    )

    assert "".join(messages) == "Converting original.pdf. done\n"


def test_build_docling_cmd_includes_useful_options(tmp_path):
    cmd = _build_docling_cmd(
        tmp_path / "input.pdf",
        tmp_path / "out",
        BackendOptions(
            docling_pipeline="vlm",
            docling_vlm_model="qwen",
            docling_image_export_mode="referenced",
            docling_ocr=True,
            docling_force_ocr=True,
            docling_tables=True,
            docling_ocr_engine="rapidocr",
            docling_ocr_lang="ch,en",
            docling_table_mode="accurate",
            docling_enrich_picture_classes=True,
            docling_enrich_picture_description=True,
            docling_enrich_chart_extraction=True,
            docling_device="mps",
            docling_num_threads=2,
        ),
    )

    assert cmd == [
        sys.executable,
        "-c",
        "import sys; sys.argv[0] = 'docling'; from docling.cli.main import app; raise SystemExit(app())",
        str(tmp_path / "input.pdf"),
        "--to",
        "md",
        "--output",
        str(tmp_path / "out"),
        "--image-export-mode",
        "referenced",
        "--pipeline",
        "vlm",
        "--vlm-model",
        "qwen",
        "--ocr",
        "--force-ocr",
        "--tables",
        "--ocr-engine",
        "rapidocr",
        "--ocr-lang",
        "ch,en",
        "--table-mode",
        "accurate",
        "--enrich-picture-classes",
        "--enrich-picture-description",
        "--enrich-chart-extraction",
        "--device",
        "mps",
        "--num-threads",
        "2",
    ]
