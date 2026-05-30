# x2md

Convert documents to Markdown.

## Install

Requires Python 3.10-3.13. Python 3.13 is recommended.

```bash
pip install x2md-0.1.0-py3-none-any.whl
```

For local development:

```bash
pip install -e .
```

The default install includes the fast, balanced, and best-quality conversion engines.
The experimental RapidDoc engine is optional:

```bash
pip install "x2md[rapiddoc]"
```

RapidDoc pins `pdfminer.six`, so install it through the `rapiddoc` extra above
instead of installing `rapid-doc` into an existing environment by hand.

Optional external tools (auto-detected, only needed for specific formats):

- `ffmpeg` — audio/video transcription
- `tesseract` — OCR for scanned PDFs / images

Run `x2md --check-deps` to see what's installed and how to get the rest.

## Usage

```bash
# single file → writes file.md next to it (or prints to stdout when piped)
x2md report.pdf
x2md report.pdf -o out.md
x2md slides.pptx > slides.md

# directory (recursive), mirror structure into ./out
x2md ./docs -r -O ./out              # skips existing .md outputs by default
x2md ./docs -r -O ./out --overwrite  # reconvert and overwrite existing .md outputs
# same-name inputs are disambiguated: report.docx -> report.md, report.pdf -> report.pdf.md

# URL
x2md https://example.com/article.html

# stdin (must declare format)
cat file.docx | x2md - -f docx

# local drag-and-drop web UI
x2md web
x2md desktop  # local UI on a random port with API token protection

# quality/speed tradeoff
x2md report.pdf --quality fast -o report.md
x2md report.pdf --quality balanced -o report.md
x2md report.pdf --quality rapid -o report.md
x2md report.pdf --quality best -o report.md

# Chinese report PDFs with OCR and charts/tables
x2md report.pdf --language ch --ocr --charts -o report.md

# remove repeated watermark text where possible
x2md report.pdf --remove-watermark -o report.md

# parse only a page range while tuning options; pages are 0-based
x2md report.pdf --start-page 0 --end-page 2 -o sample.md
```

Large PDFs and first runs can take a while, especially when OCR, chart analysis,
or model downloads are needed. A compact progress line is shown by default. Use
`--quiet` to suppress progress output.

Useful options:

- `--quality fast|balanced|rapid|best`: choose speed versus extraction quality.
- `--ocr`: enable OCR for scanned or image-only documents.
- `--remove-watermark`: remove repeated watermark text where possible.
- `--language ch`: provide a language hint for OCR.
- `--charts` / `--no-charts`: enable or disable chart and image analysis.
- `--start-page` / `--end-page`: process only part of a PDF while tuning options.
- `--overwrite`: in directory mode, reconvert files whose Markdown output already exists.

Web UI:

- Run `x2md web` and open the local browser page.
- Run `x2md desktop` for a desktop-launcher friendly local UI with a random port and API token.
- Drag files into the page, choose quality/options, and start conversion.
- Files are processed locally on the same machine and are not uploaded to a remote server.

Image outputs:

- When a Markdown file references images, x2md writes a per-document asset folder next to it.
- For example, `report.md` uses `report.assets/...` instead of sharing one global `images/` folder.

Quality modes:

- `fast`: lightweight conversion for speed.
- `balanced`: structured document conversion for everyday PDFs and Office files.
- `rapid`: experimental RapidDoc mode for faster local PDF/Office parsing. Add `--ocr` for scanned PDFs.
- `best`: default mode; high-quality extraction without OCR by default. Add `--ocr` for scanned documents.

## Supported formats

PDF, DOCX, PPTX, XLSX/XLS, HTML, CSV, JSON, XML, EPUB, ZIP, images (PNG/JPG/…), audio (MP3/WAV/…), and plain text.

## Cross-platform notes

- All paths use `pathlib`; Windows backslashes and non-ASCII paths are handled.
- Output files are written as UTF-8 with LF line endings on every platform.
- stdout/stderr are forced to UTF-8 so non-ASCII content prints correctly on Windows.

## Repository layout

- `src/x2md/`: Python CLI, web UI, and conversion core.
- `apps/desktop/`: desktop shell and packaging work.
- `docs/architecture/`: architecture and distribution notes.
