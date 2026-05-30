# x2md Desktop

This directory is reserved for the desktop shell.

The desktop product should wrap the existing local x2md service instead of
rebuilding conversion logic in JavaScript or Rust. The current service entrypoint
is:

```bash
x2md desktop
```

That command starts the local UI on a random localhost port and protects API
routes with a per-run token. A desktop shell can launch this process and open the
returned local URL in a native window.

## Recommended Stack

- Tauri for the desktop shell.
- Python sidecar/runtime for the x2md service.
- Existing x2md HTML UI for the first MVP.

## Development

```bash
cd apps/desktop
npm install
npm run dev
```

Build the current shell:

```bash
cd apps/desktop
npm run build
```

On macOS this produces:

```text
src-tauri/target/release/bundle/macos/x2md.app
src-tauri/target/release/bundle/dmg/x2md_0.1.0_aarch64.dmg
```

Build the bundled macOS arm64 Python runtime before creating a customer test
package:

```bash
cd apps/desktop
npm run runtime:build
npm run build
```

## MVP Scope

- start `x2md desktop`;
- show the local UI in a desktop window;
- stop the child process when the app exits;
- package macOS and Windows separately.

The current checked-in shell starts the Python x2md service, shows startup
diagnostics, and navigates to the local web UI when the service reports its URL.
It is still not a customer-ready offline bundle because the Python runtime and
wheelhouse are not packaged into the app yet.

## Python Runtime Resolution

The launcher resolves Python in this order:

1. `X2MD_DESKTOP_PYTHON`, then `X2MD_PYTHON`;
2. bundled app resources:
   - macOS/Linux: `x2md-runtime/bin/python3` or `x2md-runtime/bin/python`;
   - Windows: `x2md-runtime/Scripts/python.exe`;
3. repository development virtualenv:
   - macOS/Linux: `.venv/bin/python`;
   - Windows: `.venv/Scripts/python.exe`;
4. system fallback:
   - macOS/Linux: `python3`;
   - Windows: `python`.

The startup panel displays the chosen runtime source, command, and recent
stderr output. For customer distribution, build a platform-specific
`x2md-runtime` resource that already contains x2md and the selected conversion
dependencies. The first runtime build targets macOS arm64 and installs
`x2md[desktop]`, which includes fast, balanced, and rapid conversion engines but
not MinerU/best-quality.

## Non-Goals For The First Shell

- no rewrite of conversion logic;
- no Electron unless Tauri blocks packaging;
- no direct imports of Docling, MinerU, RapidDoc, or MarkItDown from the shell;
- no bundled model manager until the launcher flow is stable.

## Later Packaging Work

After the shell is working, add:

- Windows runtime packaging;
- locked wheelhouse for reproducible x2md and dependency installs;
- model cache location under the user's app data directory;
- diagnostic log export;
- optional engine isolation if dependency conflicts become frequent.
