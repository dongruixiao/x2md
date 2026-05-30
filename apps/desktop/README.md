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

## MVP Scope

- start `x2md desktop`;
- show the local UI in a desktop window;
- stop the child process when the app exits;
- package macOS and Windows separately.

## Non-Goals For The First Shell

- no rewrite of conversion logic;
- no Electron unless Tauri blocks packaging;
- no direct imports of Docling, MinerU, RapidDoc, or MarkItDown from the shell;
- no bundled model manager until the launcher flow is stable.

## Later Packaging Work

After the shell is working, add:

- embedded Python runtime;
- locked wheelhouse for x2md and dependencies;
- model cache location under the user's app data directory;
- diagnostic log export;
- optional engine isolation if dependency conflicts become frequent.
