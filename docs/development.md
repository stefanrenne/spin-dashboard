# Development

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q

npm ci          # the versions in package-lock.json, like CI
npm test
```

- **Backend** (`tests/test_*.py`): all host paths (`/proc`, `/sys/block`, `/proc/self/mounts`,
  `disks.ini`) and the data directory are redirected to a temporary folder by the `sd` fixture,
  which also pins `TZ` to `Europe/Amsterdam` so day boundaries are predictable. External commands
  (`hdparm`, `fatrace`) are not called; the logic around them can be tested on its own
  (`parse_hdparm`, `poll_once`, `handle`, `LINE`, `migrate`).
- **Frontend** (`tests/js/`): the dashboard runs in jsdom with a mocked `fetch` that serves
  `days.json` and day files.

## Running locally without Unraid

With a fake `hdparm` and your own `disks.ini` you can run the whole app on a laptop:

```bash
mkdir -p /tmp/sd/{emhttp,data,bin}
printf '["disk1"]\ndevice="sda"\n' > /tmp/sd/emhttp/disks.ini
printf '#!/bin/sh\necho " drive state is:  active/idle"\n' > /tmp/sd/bin/hdparm && chmod +x /tmp/sd/bin/hdparm
PATH=/tmp/sd/bin:$PATH DATA_DIR=/tmp/sd/data EMHTTP_DIR=/tmp/sd/emhttp \
  WATCH=/tmp PORT=8089 python3 app/spindash.py
```

`fatrace` needs root and a Linux kernel; without `fatrace` the watcher logs an error and the rest
keeps working. Old-format files (`spin.csv`, `who.csv`, …) placed in `DATA_DIR` are migrated on
start, which is a quick way to try the dashboard on real data. For the frontend only: open
`http://localhost:8089/?demo`. Force a language with `?lang=de` (en, nl, fr, de, es); otherwise the
UI follows the browser language or the last chosen language (`localStorage`, key `spindash.lang`).
The pools left out of Live activity are stored per browser under `spindash.feedHide` (a JSON list of lane keys). The card layout from Settings is stored per browser under `spindash.cards`: a JSON list of
`{id, hidden, size}` in display order (`size` is `half` or `full`; missing or unknown means the card's default,
from `data-half` in the HTML) (ids `kpi`, `lanes:<pool>`, `days`, `hist`, `causes`, `src`, `actR`, `actW`, `rest`, `feed`, `events`); unknown ids are
ignored and missing cards are added at the end. The old id `act` (one Active drives card, before the split) is read as
`actR` and `actW` in its place, with the same `hidden`. The timeline is one card per pool and the array, `lanes:array`,
`lanes:cache`, …; the old single id `lanes` marks where they go: when the data is loaded every pool without a card gets one
there (or after the last timeline card), with the `hidden` and `size` of the old card, and `lanes` is dropped. The card of a
pool that is no longer in the data stays in the list but is not shown. This only concerns `localStorage`, not `DATA_DIR`. On a wide screen every card with `data-half`
(by default all but `kpi`, the timeline cards, `srcLanes` and `events`; Settings can make any card small or large) takes
half the width, and two small cards in a row sit side by side; below 820px every card is full width.
`cardLayout()` computes where each card lands (full, left, right, or alone in its row) for the icons in Settings.
Key figures, Spin-ups per day, Spin-ups per hour of day (at least the last 7 days) and Spin time and rest per drive are
computed from `data/spins.csv` (all days), per lane as on the timeline (a merged pool counts once); only the
spinning hours in Key figures add up the physical drives. "Woke quickly" in Spin time and rest per drive counts
spin-ups within 30 minutes of going to standby (`QUICK_MS`).
`proc` and `ctr` lines (see `docs/data-formats.md`) feed the process names and the start/stop markers;
`/data/containers.json` feeds Settings › Paths per container, and `?demo` uses `demoContainers()`. Without a
Docker socket the backend logs one line and the card says so; tests use `FakeDocker` in `tests/conftest.py`,
a Docker API on a Unix socket.
The source profile, the Overview (ⓘ) and the drive detail pages share one dialog (`#profile`) with its own period
(`det`, starting at the dashboard's; its buttons never change the dashboard) and a view stack:
clicking a `.srcbtn` or `.drvbtn` inside the dialog pushes a view, "Back" pops it, and a click from outside starts
a new stack. Drive pages work on the spin lines of all days and group drives by lane key (the array, or a pool
and its members) regardless of the merge setting; activity uses `devsMatcher()`, the same rule as the lanes.
The source cards (Timeline per source, Kept awake per source, Activity per source, Rhythm per source and the profile dialog) share one
index per render (`sourceIndex()`): per source the spin-ups it caused, reads and writes per lane, every access
in the window and the kept-awake time. Kept awake: within each spinning period of a lane, every access gets the
time since the previous access (or since the spin-up), so per period the parts add up to the time from spin-up
to the last access. Rhythm (`rhythmOf()`) merges accesses within 2 minutes; a fixed interval needs at least 6
moments with 60% of the gaps within 15% of the median, a daily time at least 3 days whose first activity falls
within 30 minutes of the median. Because the same file is logged at most once per `DEDUP_SECONDS`, anything
more frequent shows as that interval. The comparison uses the period of the same length right before the shown one,
and only when the data covers all of it; "All" has no comparison. Lane grouping is stored under `spindash.group` as
`{merge, compose}`: `merge` (default `true`) shows pools and the array as one lane each, `compose` (default `false`)
counts the containers of one Docker Compose project (the `project` label from `/data/containers.json`) as one source
in the source cards and the profile. Both only change the view; the day files stay per drive and per container.
Hidden paths are a JSON list of patterns (`*` anything, `?` one character), stored on the server in
`DATA_DIR/spindash-settings.json` via `/data/settings.json` (see `docs/data-formats.md`), so every browser
sees the same list. The frontend loads them before the first data fetch and saves them on every change.
A copy stays in `localStorage` under `spindash.hide`; it is used in `?demo` mode and when the server
cannot be reached, and moved to the server once if the server has no list yet. A failed save is
shown under the field.
A pattern with a leading `/` matches the full path and its `/mnt/user/...` form; without it, the path inside
the share. Hidden activity is filtered out after loading (`data.allAct` → `data.act`), also for live events.

## README screenshots

The screenshots in `docs/images/` must always match the current dashboard (see `CLAUDE.md`). They are
made from the sample data (`?demo`, English, the 7-day view) at 1200 px wide and 2× scale, in light and
dark, with `scripts/screenshots.mjs`:

```bash
npm install --no-save playwright && npx playwright install chromium
node scripts/screenshots.mjs
```

It writes `screenshot-light.png` / `screenshot-dark.png` (the whole page) and
`active-drives-live-activity-light.png` / `active-drives-live-activity.png` (the reading, writing, spin
duration and live activity cards). Playwright is deliberately not in `package.json`, so CI and a normal
`npm install` do not need it. Check the new images before committing and update the alt texts in the README.

## Testing on Unraid

```bash
docker build -t spin-dashboard:dev .
docker run -d --name spin-dashboard-dev --privileged --pid=host -p 8090:8089 \
  -v /mnt:/mnt:ro,slave -v /var/local/emhttp:/emhttp:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v /mnt/user/appdata/spin-dashboard-dev:/data \
  spin-dashboard:dev
docker logs -f spin-dashboard-dev
```

Note: do not run two instances with the same `DATA_DIR`.
