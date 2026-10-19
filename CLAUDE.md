# CLAUDE.md

Context for Claude (and other developers) working on this project.

## What this is

Spin Dashboard: an Unraid app (a single Docker container) that shows when the HDDs on an
Unraid server spin up and **why**: which file was opened and by which process
(a container, Unraid itself or a user via SMB/shell). Distributed through Community Applications.

## Structure

| Path | What |
|---|---|
| `app/spindash.py` | The whole backend process: poller, watcher (fatrace), web server, housekeeping. Stdlib only. |
| `app/static/index.html` | The dashboard. A single file, vanilla JS, no build step. |
| `tests/` | `pytest` for the backend, `tests/js/` for the frontend (node:test + jsdom). |
| `unraid/` | Community Applications template and icon. |
| `scripts/screenshots.mjs` | Recreates the README screenshots from the sample data (Playwright, not in `package.json`). |
| `docs/` | Architecture, data formats, development, publishing, design decisions. |
| `todo.md` / `completed.md` | Open and finished work. Update them when you pick something up or finish it. |

## Commands

```bash
python -m pytest -q          # backend tests
npm install && npm test      # frontend tests
docker build -t spin-dashboard .
# running locally without Unraid: see docs/development.md
```

Run both test suites before every commit. CI (`.github/workflows/ci.yml`) does the same and only
then builds the image.

## Hard rules

1. **Never wake a sleeping drive.** Spin state only via `hdparm -C` or
   `smartctl -n standby`. No `stat`, `ls` or `smartctl -a` on HDDs from the poller.
   An `os.path.isdir()` in the watcher is only allowed after an event, when the drive is already spinning.
2. **Never write under `/mnt`.** `/mnt` is mounted read-only; all output goes to `DATA_DIR`.
3. **Do not log our own writes** (our own PID and our own file names are skipped),
   otherwise the dashboard keeps itself awake when appdata lives on an HDD.
4. **Stdlib only in the backend.** No pip dependencies in the image.
5. **Data formats are a contract** between backend and frontend and with existing installations.
   Only change them in a backward-compatible way; see `docs/data-formats.md`.
6. **Host paths via module constants** (`PROC`, `SYS_BLOCK`, `MOUNTS_FILE`, …) so tests can replace them.
7. **Every change to the stored data comes with a migration.** When what is written to `DATA_DIR`
   changes (a line type or its fields in the day files, file names or layout, the settings file),
   write a migration in the same change that brings existing installations' data to the new form.
   Like `migrate()` it runs on start, is safe to run again, never loses data (originals go to
   `DATA_DIR/legacy`), logs what it did, and has a pytest with data in the old form. Describe it
   under "Migration" in `docs/data-formats.md`. A purely additive change (a new line type or file
   that old data simply lacks) needs no conversion, but still gets an entry under "Migration"
   saying so and why, and a test that data in the old form still loads.

## Conventions

- Everything is **English**: code comments and docstrings (backend, frontend, tests, scripts),
  log lines, Markdown documentation (this file, `docs/`, `todo.md`, `completed.md`), the README and
  the CA template. Only UI texts in `I18N` and the Dutch values that are part of the data format
  (`geopend`, `gebruiker`, ...) stay as they are.
- The UI is multilingual (en, nl, fr, de, es): all texts live in `I18N` in `index.html` and go
  through `t('key')`. A new text gets a key in **every** language; a test checks this.
  Do not use `t` as a local variable name in code that also translates.
- Data: one CSV per day in `DATA_DIR/days`, lines `epoch,type,...` (see `docs/data-formats.md`).
  Times in epoch seconds; the day is the container's local date. The frontend shows local time.
- Small, focused changes. New logic gets a test.
- Frontend: no frameworks, no external assets except Google Fonts with a fallback.
- **README screenshots must always match the current dashboard.** Any change that is visible in
  the UI (cards added, removed, renamed or rearranged, new layout) refreshes the screenshots in
  `docs/images/` and their alt texts in the README in the same commit, with
  `node scripts/screenshots.mjs` (see `docs/development.md`).

## Pitfalls we already ran into

See `docs/decisions.md` for the background. In short:
- Containers see other containers' cgroups as `0::/../<id>` (cgroup namespace). Match on the 64-hex ID.
- Access via `/mnt/user` goes through `shfs`; the real opener is found via `/proc/*/fd` and inodes.
- `fatrace` reports folders without a trailing slash; the watcher adds it.
- Auto-updaters (What's Up Docker) can remove this privileged container and fail to restore it.
