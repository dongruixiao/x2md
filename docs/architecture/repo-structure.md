# Repository Structure

x2md uses a lightweight monorepo layout: one repository, multiple clearly owned
surfaces. The Python package remains the source of truth for conversion logic;
desktop packaging layers wrap it instead of reimplementing document conversion.

## Layout

```text
x2md/
├─ src/x2md/               # Python core package: CLI, web UI, converters
├─ tests/                  # Python package tests
├─ apps/
│  └─ desktop/             # Tauri desktop shell, packaging, app assets
├─ docs/
│  └─ architecture/        # Design notes and packaging decisions
├─ dist/                   # Local build artifacts, ignored by git
└─ pyproject.toml          # Python package metadata
```

## Ownership

- `src/x2md`: shared product core. CLI, local web app, conversion backends, and
  desktop-safe `x2md desktop` launcher live here.
- `apps/desktop`: desktop app shell only. It starts or embeds the local x2md
  service and displays the UI. It must not contain conversion logic.
- `docs/architecture`: decisions that affect packaging, runtime isolation, and
  cross-platform distribution.

## Desktop Runtime Boundary

The desktop shell treats Python as a runtime dependency, not as app logic. Its
launcher resolves a Python executable from environment overrides, bundled app
resources, the development `.venv`, or the system fallback. Customer installers
should eventually ship a platform-specific `x2md-runtime` resource with the
locked wheel set already installed.

This keeps the conversion API stable:

```text
Tauri shell -> Python runtime -> python -m x2md desktop -> local FastAPI UI
```

The shell may report startup diagnostics, restart the service, and own installer
packaging, but conversion behavior stays in `src/x2md`.

## Why Not Split Repositories

Keeping the desktop shell and Python package in one repository keeps releases
coordinated:

- the desktop app always knows which x2md CLI/web API it targets;
- tests can cover CLI and desktop-service behavior in one CI run;
- release tags can describe both package and desktop changes;
- the repo can later add per-engine workers without moving code across repos.

If the desktop product grows into a separate team or release cycle, `apps/desktop`
can be split later without changing the Python package layout.

## Future Engine Boundary

The current Python package calls backends directly. If dependency conflicts grow,
add worker processes behind the same converter interface:

```text
src/x2md/
├─ converter.py            # stable public conversion API
└─ engines/
   ├─ markitdown_worker.py
   ├─ docling_worker.py
   ├─ rapiddoc_worker.py
   └─ mineru_worker.py
```

The desktop app should continue to talk to `x2md desktop` or a local x2md service,
not to individual engine libraries.
