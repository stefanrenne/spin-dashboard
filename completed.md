# Completed

Newest first.

## 2026-10-22: PR #2, Process column in Events (RuudPuts)
- Built on the current `main` instead of merging the branch (it was based on 18 October and conflicted): the Process column (left of Cause) with the container, the process inside it and its icon; the source label moved there from the cause cell, so it is not shown twice. Hidden without who lines
- Icons: `docker_icons.xml` from the PR does not exist on the own server; Unraid keeps every icon as `<name>-icon.png` under `/emhttp/plugins/dynamix.docker.manager/images` (tmpfs), so `containers.json` points to `data/icons/<name>.png` and the backend serves them, and the page loads no images from other sites. An icon that fails to load is removed
- `container_name()` asks Docker for all containers at once (`/containers/json?all=1`), keeping the retry delay for IDs Docker does not know
- Not taken over: the CI change that pushes a `pr-<n>` image for pull requests (forks cannot push to this repository's registry, and Dependabot PRs would fill it); PRs are built without pushing, as before

## 2026-10-22: faster processing in the browser (no database)
- Measured on 25 days of the own server (25 MB, ~33,000 who lines a day): downloading (~0.1 s a day) and parsing the CSV (~30 ms for 30 days) cost little; the time goes to processing in the browser. A database would only help if the server computed the cards, so the CSV files stay
- Paths are split once per distinct path (`partsOf`), `normPath` skips its regex for paths without a trailing slash, `mergeActivity` looks for duplicates per path instead of with five string keys per who line and only sorts when the lines are out of order, the pools with activity are found once per load, and the lane of an event is looked up once per drive name (`laneFinder`)
- Result (median of 3, headless Chromium): first load 783 → 440 ms, switching to 30 days 1233 → 827 ms, a redraw of 30 days 432 → 328 ms, "All" 449 → 332 ms, 24 hours 181 → 137 ms. What is left when loading is mostly building ~800,000 event objects in `mergeActivity`

## 2026-10-21: reads and writes per pool, pool filters on more cards
- Reads per drive and Writes per drive are a card per pool and the array (`actR:<key>`, `actW:<key>`), like the timeline: the pools whose drives spin plus the pools with file activity. A saved layout gets them where the single card stood, with its visibility and size
- The pool checkboxes of Live activity are a reusable filter, now also on Activity per source, Rhythm per source, Timeline per source and Events (replacing the drive dropdown); each card keeps its own choice per browser (`spindash.srcHide`, `spindash.rhythmHide`, `spindash.srcLanesHide`, `spindash.eventsHide`). Kept awake per source is left as it was

## 2026-10-21: Unassigned Devices and bytes per container
- Drives mounted by Unassigned Devices at `/mnt/disks/<label>` are found in the mount table, named `disks/<label>`, and followed like pools: spin state, file access, their own lane and card, drive page; `/mnt/remotes` shows as a network share in Paths per container. Not seen on the own server (no such drives there), tested with mount tables
- Bytes per container from the host's cgroup v2 `io.stat` (`/proc/1/root/sys/fs/cgroup`), added up and written every 10 minutes as `cio` lines; shown in the container profile (read/written, per drive) and on the drive pages (per container). Checked on the own server that `io.stat` is readable there
- SAS testing stays in `todo.md`, now with a concrete test plan

## 2026-10-21: Live activity per pool
- Checkboxes above Live activity, one per pool (and the array) with activity, choose which pools the feed shows (also for streamed lines); kept per browser in `spindash.feedHide`; hidden when there is only one

## 2026-10-21: Dependabot PRs merge themselves
- Dependabot groups a week's updates into one PR per ecosystem (actions, npm, pip, docker), so they do not keep putting each other behind `main`; a PR that is behind is merged as it is, unless it changes a workflow file: that one needs a rebase, which Dependabot only takes from a user with push access (so from `AUTOMERGE_TOKEN`, or by hand)
- CI builds the image on pull requests too (without pushing), so updates of the build actions and the base image are tested before they land
- `dependabot-automerge.yml`: when CI passed on a Dependabot PR, it is squash-merged (only the tested commit) and CI is started on `main` to build `latest`; failing PRs stay open. No repository setting or branch protection needed, so pushing to `main` directly keeps working

## 2026-10-21: version and changelog
- `CHANGELOG.md` for users, with 0.9.0 as the first version (everything up to now); `docs/publishing.md` says how a release is made
- CI puts the version in the image (`SPINDASH_VERSION`: `X.Y.Z` for a tag `vX.Y.Z`, `main-<sha>` otherwise); it is in `/health`, the start log and at the bottom of the page with a link to the changelog

## 2026-10-21: "All" measured again, no summary endpoint
- With SSD pools logging only writes, the own server's day files grow ~75 KB an hour (~1.8 MB a day, against ~6 MB with the reads of `frunk` and 0.3–0.5 MB with HDDs only): "All" over 30 days is ~54 MB, ~7 MB gzipped, and since live days are fetched in parts every day is downloaded and parsed only once. A per-day summary endpoint would mean rebuilding most cards on summaries; not worth it at this size, so not built

## 2026-10-21: features
- Refresh right away when a hidden tab becomes visible again
- CSV export: reads and writes per drive per hour (`io`) and the SMART wear (`wear`), in four new columns at the end
- SSD lanes can be stepped through with the arrow keys, bar by bar, with the same text as the tooltip
- The SSD page says how the wear went in the period and, at the current pace, in about how many years the rated endurance is reached (from written since new and the percentage used)
- Webhook messages, both optional: reads and writes above `quiet.io_mb` MB inside the quiet hours (once per lane per window; SSD pools can be chosen too), and an SSD that wrote more than the wear limit in a day (`wear_notify`, once per SSD per day)
- Not done in this round, on purpose: Unassigned Devices and bytes per container stay in `todo.md`; testing SAS drives needs the hardware

## 2026-10-21: watcher and data quality
- Not done, on purpose: the noisy day file of 2026-10-20 on the own server (12.9 MB, from before the watcher fixes) is left alone and ages out after 30 days
- Writes by `dockerd` in `docker/containers/<id>/` (a container's log and config) count for that container, by name, instead of `dockerd` with a hex path as the only clue
- The ` (deleted)` suffix of fatrace is stripped from paths
- `/health` reports per watched filesystem whether its fatrace runs (`watcher.running`); a fatrace that cannot start because of the fanotify limit gets a hint in the log (`fs.fanotify.max_user_groups`)
- Docker events: after a reconnect the stream continues from the last event it saw (`since`), and an `oom` kill is logged as a `ctr` event (shown like a stop, labelled "out of memory")

## 2026-10-21: security
- Optional `SETTINGS_TOKEN`: saving settings and testing the webhook need it in the `X-Settings-Token` header; the settings answer then says `"locked": true` and each browser asks for the token once (kept in `localStorage`)
- The webhook URL never leaves the server in full: browsers get scheme and host only, and that masked value sent back keeps the stored URL
- README › Security and the CA template say the dashboard has no login and shows every accessed path: keep it on the LAN or behind a reverse proxy with a login

## 2026-10-21: performance
- The live days are fetched in parts: `/data/days/<day>.csv?from=N&file=ID` sends only the bytes added since (whole lines), and the waiting `io`/`wear` lines come from `/data/pending.csv`. The browser keeps every day as parsed lines and only parses what is new, instead of downloading today's file and parsing every loaded day again every minute. Older servers and frontends keep working as before
- `/data/spins.csv` keeps the summary of each day file and only adds the waiting lines
- SSD pools log only writes unless "also reads" is chosen per pool (`ssd_reads`): a read from an SSD wakes and wears nothing, and the opens of program files made the day files ~6× bigger
- `write_who()` forgets old keys in time order (`OrderedDict`) instead of walking all of them for every line during a big scan
- The poller asks all HDDs at the same time and keeps a fixed rate (`next_poll()`)

## 2026-10-21: development chores from the review
- `docs/architecture.md`: the diagram is English again, with the newer parts (io buffer, SMART, one fatrace per filesystem, the guarded shfs lookup, `/data/notify-test`) and straight borders
- README › Permissions: what the Docker socket is used for and when `smartctl` runs; `DEDUP_SECONDS` in the README and the CA template
- `package-lock.json` is committed; CI uses `npm ci` with the npm cache
- Dependabot for actions, npm, pip and the Docker base image; CI rebuilds `latest` every Monday with a freshly pulled base image (`docs/publishing.md`)
- Tests: the watcher loop body is `watch_once()` and tested (restart on a mount change or a stopped fatrace); a closed SSE connection is forgotten. The SSE endpoint and `fd_snapshot()` turned out to be covered already. The test fixture now also resets `procs` and `streams`, which made a stream test depend on the one before it
- The page sets `data-ready` on `<html>` after its first load; the frontend tests and the screenshot script wait for it instead of a fixed time (frontend tests ~50 s → ~32 s; the rest is jsdom building the page per test)

## 2026-10-21: the bugs from the review
- "Per day" counts from the first measurement (the first `io` line of any drive), so periods that start before the update no longer divide by days that were never measured; at least an hour
- Our own tools are never logged: `run_own()` registers the PIDs of `hdparm` and `smartctl`, and `fatrace` registers its own (rule 3)
- The waiting `io`/`wear` lines are written when the drive of `DATA_DIR` spins or is an SSD (`data_dir_devices()` from mountinfo), not when any HDD spins; the start log names that drive
- The lookup through `/mnt/user` for the container behind an shfs access only happens when no sleeping drive of the share can be touched (`user_share_lookup_safe()`, rule 1)
- `WATCH` works outside `/mnt` again (also on tmpfs, as in `docs/development.md`)
- The live stream keeps the SSD pools in Reads/Writes per drive
- A server with only SSDs shows its drives instead of "no data"; the summary counts HDDs and SSDs
- "Written since new" uses the unit in the SMART attribute name (Intel `Host_Writes_32MiB`, `Lifetime_Writes_GiB`, …)
- `container_name()` no longer keeps a short ID as name when Docker did not answer once; it asks again after a minute

## 2026-10-21: `docker stop` writes the waiting lines (checked on the server)
- Checked on real hardware that `stop()` (SIGTERM) works: the containers with the handler stopped with exit code 0 within Docker's timeout (the version without it: 143), and an `io` line that only existed in memory at the stop (17:50:55 on 2026-10-20, all HDDs asleep) is in the day file

## 2026-10-20: one fatrace per filesystem
- With file access on for an SSD pool that holds Docker's data-root, the watcher also followed the container overlay mounts (health checks and binaries as noise) and Docker volumes that bind a folder of the pool (every event twice, under two paths). `target_mounts()` now skips overlay, tmpfs and other non-drive filesystems and keeps one mount per source: the shortest under `/mnt`
- fatrace still saw Docker's storage through the real filesystem (some 25,000 opens of binaries and libraries a minute). Opens in Docker's storage are no longer logged, writes to a layer become one line per layer folder, the container logs stay as they are

## 2026-10-20: SSD wear
- What the SSDs write per day: a fifth key figure (compared with the previous period, from hourly `io` totals that `spins.csv` now carries), on each SSD lane, in the overview and on the drive pages
- SMART wear per SSD (`wear` lines: percentage used and written since new, NVMe and common SATA attributes), asked hourly and only of SSDs, shown on the drive pages and in the overview
- Settings › File access on SSD pools: a limit in GB per day per SSD (`wear_warn`, default 100, empty = never); above it the tile, the lane, the overview and the page are marked
- Additive changes recorded under "Migrations by change"; background in `docs/decisions.md`; screenshots refreshed

## 2026-10-20: activity on every drive, SSDs included, and a timeline per pool
- The poller names every drive Unraid knows with its kind (`hdd`/`ssd` in the `disk` lines) and writes what every drive read and wrote per poll (`io` lines, from `/sys/block/<dev>/stat`, which never wakes a drive). While every HDD sleeps these lines wait in memory and are served along with the day file, so a busy SSD never makes the dashboard keep a drive awake
- "Timeline per drive" is one card per pool and the array, each hidden, moved and resized on its own; an old layout puts them where the single card was. HDD lanes show the data read and written below the spin wave, SSD lanes a read/write graph with totals. SSDs stay out of the spin figures, quiet hours and causes
- Settings › File access on SSD pools: per SSD pool, follow which file is opened by whom (`ssd_watch`, off by default); it then also shows in Reads/Writes per drive and in the sources
- The overview and the drive pages cover SSD pools and drives too: read, written, the busiest minute and a read/write graph; HDD pages show their reads and writes as well
- Additive changes to the day files and the settings file, recorded under "Migrations by change"; screenshots refreshed

## 2026-10-18: quiet hours
- Settings › Quiet hours: a window (may run over midnight) for all drives or chosen pools / the array, saved on the server (`quiet` in the settings file). The timeline per drive shades it and spin-ups inside it get a "quiet hours" label in Events
- Optional webhook: the poller POSTs a JSON message on a spin-up in quiet hours (at most once per lane per 10 minutes, in its own thread), with a "Send a test" button (`/data/notify-test`). Unraid's own notifications are not used, see `docs/decisions.md`
- Additive change to the settings file, recorded under "Migrations by change"

## 2026-10-18: Compose projects as one source
- Settings › Drives has a switch to count the containers of one Docker Compose project as one source (off by default, remembered in `spindash.group`): the source cards and the profile then show the project with a "stack" label, its containers in the tooltip, and the start/stop events of all its containers. The live feed stays per container

## 2026-10-18: CSV export
- A CSV button in Events downloads the selected period: spin-ups, standby and measured state per drive, file access with source, process, action and path, and container events (a restart as die + start, with the exit code). Hidden paths stay out; columns and values are English; format in `docs/data-formats.md`

## 2026-10-18: faster shfs lookups
- `via_user_share` reads all `/proc/*/fd` links once per second into a map by file name instead of once per `shfs` event; a snapshot is only reused for events from before it was taken

## 2026-10-18: health endpoint
- `/health` reports the poller (seconds since the last successful poll, last error), the watched mounts and running `fatrace` processes, the Docker event stream and the background threads; `503` only when the poller is stuck or a thread stopped
- The Docker `HEALTHCHECK` uses `/health` instead of fetching the whole page (which also caused the "connection reset" tracebacks seen on a real server)

## 2026-10-18: file paths by keyboard
- File rows in Reads/Writes per drive, the profile and the live feed can be reached with Tab; focus shows the full path in the overlay, screen readers hear it as the label, Escape closes the overlay, and focus stays on the same path when the feed or the cards redraw

## 2026-10-18: one word for the hardware
- The English UI says "drive" everywhere (the events table, the filter, the subtitle and the empty-state text said "disk"); a test keeps it that way. The other languages already used one word. README wording follows; screenshots refreshed

## 2026-10-18: period buttons on a phone
- The period buttons fill the width on a narrow screen (wider where the text is longer, text may wrap to two lines) instead of scrolling with a hidden scrollbar, so "All" is never cut off

## 2026-10-18: release checks
- Tested on an array-less setup with a Btrfs HDD pool (Unraid 7.3.3, pool `trunk` with four HDDs, NVMe pool `frunk` for appdata): the image runs, watches `/mnt/trunk` and logs spin state, file access and container events
- Documented in `docs/publishing.md` why the image is amd64 only

## 2026-10-19: the detail dialog has its own period
- The period buttons in the detail dialog no longer change the dashboard: the dialog keeps its own period (starting at the dashboard's), fetches missing days quietly and calculates its pages for that window
- Previous / Now / Next buttons in the dialog step a whole period back or forward, like on the dashboard; more space below the controls
- docs/development.md describes the dialog's own period

## 2026-10-19: English comments
- All code comments and docstrings are English now (backend, frontend, tests, scripts, Dockerfile); CLAUDE.md requires English everywhere except the UI texts in `I18N` and the Dutch values of the data format. Only comments changed: the code of every file is identical before and after

## 2026-10-19: links to drive pages everywhere
- The pool/array labels at a container's paths and in the Overview, the drives in a source profile, the line under each source in Activity per source, the drive heading in Reads/Writes per drive and the first column of Spin time and rest per drive all open the drive page
- A pool without hard drives (such as an NVMe pool) gets a page that says it is not watched and lists the containers that map it

## 2026-10-19: drive pages from everywhere, period in the dialog, healthcheck examples
- Drive names in Timeline per drive and in Events open the drive page (a merged pool or the array as a whole, otherwise the single drive)
- The detail dialog has the period buttons (last hour, 24 hours, 7 days, 30 days, All); they switch the dashboard's period, so the right days are loaded, and recalculate the open page
- README: `docker run` and Docker Compose examples show the healthcheck on `/health`

## 2026-10-19: overview and drive detail pages
- An ⓘ button next to Settings opens an Overview: every pool and the array with their drives (state, spin-ups, % active) and every Docker container (status, Compose project, location labels, activity in the period)
- Every container, pool, the array and every single drive has a detail page; a drive page shows key figures, the drives of a pool or the array, who reads and writes, what woke it, folders, files and which containers map it. Pages open in one dialog with a Back button; a container without activity in the period still gets a profile
- Settings › Paths per container is gone: the paths are on the container's profile and in the Overview

## 2026-10-19: Paths per container: location labels
- Each folder gets a label with the pool or "Array" it lives on according to the share settings, both with an arrow when the mover moves it; every pool and the array has its own colour (white text at 4.5:1 or better, checked by a test). The tooltip says when the folder can be on a hard drive
- The same folder mapped more than once (a volume on several targets) shows once
- The container profile lists the container's paths with the same labels and colours; for a Compose project the paths of each container under its name

## 2026-10-18: open the source profile from everywhere
- Source names in the live feed (the process name stays behind it), the source label next to a cause and in the file list of Events, and the processes in Reads/Writes per drive open the source profile, like in the source cards. A source that does not occur in the selected period (the feed always shows the newest) stays plain text

## 2026-10-18: validated on a real server (Unraid 7.3.3)
- `ctr` lines arrive from Docker's event stream (start, die with exit code 137, healthy); `shares.ini` uses `useCache`/`cachePool`/`cachePool2` as expected
- `proc` lines work: Plex opening an episode on the HDD pool is logged as `proc,<pid>,Plex Transcoder` right before its `who` line
- Fixed: containers that use named Compose volumes binding a folder (`o=bind`, `device=/mnt/...`) were missing from Paths per container; such a volume now counts as its folder
- Fixed: a client that hangs up (the healthcheck) no longer prints a traceback in the log

## 2026-10-18: card size and position in Settings
- Every card can be small (half width) or large in Settings › Cards; the choice is saved with the layout (`size` in `spindash.cards`, older layouts keep the default sizes) and reset by "Restore default layout"
- Each card shows where it lands on a wide screen with a small icon (whole width, left, right); two small cards side by side are connected in the list, and clicking the icon swaps them, also across a hidden card in between
- Screen readers hear the position with the card name; changes are announced
- Settings › Paths per container moved below Cards and shows only the host path with an HDD label

## 2026-10-18: Docker: storage, lifecycle and processes
- Settings › Paths per container (below Cards): the host paths under `/mnt` each container maps (Docker API), with an HDD label when the folder can be on a hard drive according to `shares.ini` (pool, array, mover direction). New endpoint `/data/containers.json`. (First built as a card with advice; that added too little and moved to Settings as a plain list.)
- Container lifecycle: a `docker` thread follows Docker's event stream and writes `ctr` lines (start, die with exit code, healthy, unhealthy). A die + start within a minute shows as a restart. Marked on the source timeline, listed in the profile, and named next to the cause of a spin-up that follows within 5 minutes
- Process inside a container: a `proc` line with the fatrace process name per PID, so the feed and the profile show e.g. "plex › Plex Media Scan"
- No conversion needed for existing data (purely additive), recorded under "Migrations by change" in `docs/data-formats.md`; CLAUDE.md rule 7 now spells out how additive changes are handled
- Bytes per container deliberately not built (FUSE/shfs, cgroup namespace); see `docs/decisions.md`

## 2026-10-18: consistent card titles
- Every card title follows one pattern, "<subject> per <dimension>", and uses "drive" for the hardware: Timeline per drive, Spin-ups per hour of day, Spin-up causes per folder, Kept awake per source, Activity per source, Rhythm per source, Reads per drive, Writes per drive, Spin time and rest per drive (and the same in nl, fr, de, es)
- A test checks the pattern in every language; README screenshots refreshed

## 2026-10-18: container activity in depth
- Timeline per source: every access of the busiest sources as a tick (read/write) on the same time axis as the drives, over a band that shows when any drive spins
- What keeps them awake: per source the spinning time its accesses added by postponing the spin-down, and how often it was the last access before standby
- Rhythm: per source a 24-hour activity strip and a recognised schedule ("every 2 h", "daily around 03:30"), with a note about the 10-minute dedup
- Source profile: clicking a source name opens a dialog with its spin-ups, kept-awake time, reads and writes, rhythm, drives, a day × hour heatmap and its top folders and files
- The sample data gets radarr every 2 hours and the mover daily at 03:30, so the rhythm card has something to recognise; README screenshots refreshed

## 2026-10-18: README screenshots up to date
- New screenshots of the current dashboard (sample data, 7 days, light and dark), including Key figures, Spin-ups per day, By source and Spin duration and rest; the detail screenshot now shows the reading/writing cards, Spin duration and rest and Live activity
- `scripts/screenshots.mjs` recreates them with Playwright; `CLAUDE.md` now requires the screenshots to match the UI in the same commit as any visible change
- The sample data covers 15 days instead of 6, so the week view can compare with the previous week

## 2026-10-18: card review
- New card By source: containers and processes ranked by the spin-ups they caused, with reads, writes and the drives they touched; "By whom" moved here from What wakes the drives
- New card Spin duration and rest: per lane the median run after a spin-up, the longest rest and "woke quickly" (spun up within 30 minutes of going to standby)
- Spin-ups by hour of day now covers at least the last 7 days, and no longer repeats the spin-up count from Key figures
- Key figures: spinning hours add up the physical drives (spin-ups stay per lane)
- Reading and writing cards have "Processes" and "Files" headings again
- Live activity merges repeats of the same file into one row with ×n, shows "Unknown" instead of the drive name when the source is unknown, and says it always shows the newest events when an earlier period is selected
- Dutch: "Vorig uur" / "Volgend uur" instead of "Vorige uur" / "Volgende uur"

## 2026-10-18: key figures and spin-ups per day
- New card Key figures at the top: spin-ups, spinning hours, share of time all drives slept and the longest full rest, each compared with the previous period of the same length (arrow, change, the previous value, and better/worse in colour and for screen readers). Shown only when the data covers the whole previous period
- New card Spin-ups per day: one bar per day over all days (up to 90), the selected period highlighted, with the daily average
- New endpoint `/data/spins.csv` with only the `disk`/`state`/`spin` lines of every day (cached per day file, gzip, ETag), so this needs no extra day files; older servers fall back to the loaded days

## 2026-10-18: Active drives split into reading and writing
- Active drives is now two cards, "Active drives: reading" and "Active drives: writing", each listing only the drives with activity in that direction, busiest first, with the share of all file events on that drive
- On a desktop every card except the timeline and Events takes half the width; on a phone (below 820px) every card stays full width
- A saved card layout with the old Active drives card gets both new cards in its place, hidden if it was hidden

## 2026-10-18: hidden paths stored on the server
- Settings › Hidden paths is now saved in `DATA_DIR/spindash-settings.json` through `GET`/`POST /data/settings.json`, so every browser and device sees the same list
- Input is validated (list of strings, at most 200 patterns of 500 characters, JSON only, body at most 64 KiB); the watcher skips the settings file like the day files
- An existing per-browser list (`spindash.hide`) is moved to the server once when the server has none; `localStorage` stays the fallback for `?demo` and when the server is unreachable, and a failed save is shown under the field

## 2026-10-16: README screenshots
- New README screenshots of the current dashboard (sample data, 7 days, light and dark), and a light version of the Active drives / Live activity screenshot

## 2026-10-16: hidden paths
- Settings › Hidden paths: one wildcard pattern per line (`*` anything, `?` one character), remembered per browser (`spindash.hide`)
- `/mnt/user/appdata/*` also hides the same files seen as `/mnt/<pool>/appdata/...`; a pattern without a leading slash matches inside the share (`*.log`, `appdata/plex/*`)
- Hidden activity disappears from the feed, Active drives, the rankings, session lists and spin-up causes, also when it streams in live. The day files stay complete
- The field shows how many file events are hidden

## 2026-10-16: pools and the array as one lane
- Each pool and the array show as one lane, one event row and one entry in the disk filter. A pool lists its member devices under the name, the array its number of drives
- A combined lane spins when any of its drives spins; the tooltip says how many were spinning at most, e.g. "spinning (2 of 3)"
- One switch under Settings › Drives, on by default, remembered per browser (`spindash.group`). Frontend only: the day files are unchanged
- Clicking inside the settings dialog's own padding no longer closes it; only clicks on the backdrop do
- The first option of the events filter names what it covers: "All drives", "All pools" or "All drives and pools"

## 2026-10-16: settings
- A gear button at the top right (where the language choice was) opens Settings, a dialog with the language choice and the list of cards
- Each card can be hidden or shown and moved by dragging its handle (mouse or touch) or with the up/down buttons; the layout is stored in the browser (`spindash.cards`) and survives new cards being added. "Restore default layout" resets it
- The two half-width cards (spin-ups by hour, what wakes the drives) sit side by side when they are next to each other; on their own they take the full width
- Keyboard and screen reader friendly: Escape closes the dialog and returns focus to the gear, moving a card keeps focus on it and announces its new position

## 2026-10-16: accessibility fixes from a design audit
- Badge text (R/W in session lists and the live feed) now reaches at least 4.5:1 in both themes; it was as low as 1.8:1 (white on amber in dark mode). New tokens `--up-text` (amber that is readable as text) and `--on-accent` (text on coloured badges and the primary button)
- "User" labels and write events use `--up-text` instead of `--up` as text colour (was 3.3:1 in light mode)
- The disk timelines can be read with the keyboard: Tab to a lane, arrow keys step through its periods, Home/End jump to the first/last; the tooltip appears and a live region reads it out. Focus survives live updates
- Histogram bars have their own label for screen readers; axis and hour labels are hidden from them; the timeline SVG is hidden behind the lane's own label
- Info icons point to their explanation with `aria-describedby`, so screen readers hear it without hovering
- The kind of a source (container, user, Unraid) is also given as text, not only as the colour of its dot
- Larger hit areas: info icons 28×28, the file toggle in the events table, the "Spin-ups only" label, and the browse arrows
- All font sizes are in `rem`, so the page follows the browser's text size; nothing is smaller than 11px
- The live feed no longer wraps its time onto a second line (five items in a four-column grid)
- Live activity has its own panel below Active drives instead of being a sub-heading inside it

## 2026-10-12: fixed header layout
- Language choice top right next to the title; period and browse buttons always on their own row below it, so the menu no longer jumps when switching period
- On narrow screens the period buttons scroll horizontally instead of breaking their labels

## 2026-10-12: Cache Dirs scans of large folder trees
- Folder opens by an already stopped process without a source are not logged while Cache Dirs runs. fatrace reported these `find` runs as `unknown`, which slipped past the filter: on a real day 98.5% of all lines (46 MB of 46.5 MB), and the main reason the dashboard loaded slowly

## 2026-10-12: processing 60× faster, new loading screen
- The dashboard opens in the 24-hour view (3 day files instead of 9 on first load)
- Switching period or browsing back and forward shows the skeleton and the loading bar again, with the chosen button highlighted right away
- Finding the source of an access (`nearestWho`) scanned the whole list for a path on every call, which made processing quadratic; with 110,000 real lines it took about 2 minutes in jsdom. The who index is now sorted per path with a separate index for SMB/NFS shares, and lookups use binary search: under 2 seconds in jsdom
- The live stream inserts new lines in place instead of re-sorting all activity, and redraws at most every 300 ms after the first event
- Loading screen: a thin progress bar at the top, the current step under the title and a shimmering skeleton of the dashboard, instead of a separate panel

## 2026-10-12: faster loading
- Day files are sent gzip-compressed (about 8× smaller on real data) with an `ETag`, so unchanged days get a `304`
- The frontend fetches only the days of the shown period (plus a day of margin, plus today and yesterday); browsing back or "All" fetches the rest on demand, and the minute refresh only refetches the live days
- `days.json` includes the size per day
- Loading no longer stalls in a background tab, where `requestAnimationFrame` never fires

## 2026-10-12: loading progress
- Loading panel on first load: a progress bar that follows the downloaded bytes ("3 of 12 days loaded · 4.2 of 13.0 MB"), then "Processing the data" and "Building the dashboard"; the browser gets a frame between steps so the bar actually updates
- `days.json` includes the total size of the day files (`bytes`)
- Google Fonts no longer blocks the first paint

## 2026-10-11: live feed and browsing
- Live activity feed under Active drives: newest file events first, with R/W marker, source and a
  relative time; items new since the last refresh get a short highlight
- The feed **streams**: a Server-Sent Events endpoint (`/data/stream`) pushes every `who` line the
  moment it is logged, so the feed updates instantly instead of waiting for the minute fetch
- "Last hour" period toggle with 10-minute axis ticks
- Hovering a truncated file path in Active drives shows the full path in an overlay

## 2026-10-11: reads vs. writes and active drives
- `fatrace` filter `O+W+D<`: writes to already-open files are now logged, not just opens
- Read vs. write direction per access: `geschreven` (a `W` event or an open with write intent) next
  to `geopend` (read-only); the frontend shows an R/W badge per file and per event
- Dedup key extended with the action, so a read and a write of the same file each survive per window
- New "Active drives" panel: per drive the top write/read sources and files over the period, also
  when there were no spin-ups — answers "why is this drive never idle"
- Sample data includes write activity so the panel is visible in demo mode
- Format extended, not migrated: old `geopend` lines carry no direction and still render

## 2026-10-09: one file per day
- All data in `days/YYYY-MM-DD.csv` with lines `epoch,type,...` (disk, state, spin, who, activity)
- `state` lines at the start of each day and after a restart, so every day file can be read on its own; no false spin-ups
- Automatic migration of `spin.csv`, `disks.csv`, `who.csv` and `activity.csv`; old files moved to `legacy/`
- Retention per whole day; `/data/days.json` and `/data/days/<day>.csv` in the web server
- Markdown documentation translated to English
- Screenshot of the dashboard (sample data, light and dark) in the README
- Log messages in English; the start line shows the installed fatrace version instead of its usage text

## 2026-10-09: languages and browsing
- UI in English, Dutch, French, German and Spanish; defaults to the browser language, the choice is remembered
- Browse through time: per day (24 hours), per week (7 days) and per 30 days, with a "Now" button that is always visible and highlighted for the current period
- Shorter axis labels, thinned out on narrow screens
- Info icons with tooltips in "What wakes the drives" (root of a pool, unknown, shfs, already stopped) instead of explanation paragraphs
- Frontend tests run with `--test-force-exit`, so a failing test no longer hangs on the refresh timer

## 2026-10-08: Cache Dirs
- Processes in `IGNORE_PROCS` (default `cache_dirs`) and their children are not logged
- Source per PID determined on arrival and remembered for 30 s: events of short-lived processes no longer show up as `(al gestopt)`
- Cache Dirs also recognised via the command line and, as a safety net, by the same process name without a source
- README: `docker run` and Docker Compose examples, tip about Cache Dirs

## 2026-10-01: Unraid app
- Everything merged into one container (`app/spindash.py`): poller, fatrace watcher, web server, housekeeping
- Pools and disk names from `disks.ini` instead of `zpool`/`lsblk` on the host
- `smartctl -n standby` as a fallback for `hdparm`
- Our own writes are not logged
- Cleanup by age (`RETENTION_DAYS`) instead of by file size
- Community Applications template, icon, Dockerfile, CI (tests → image to GHCR)
- Unit tests: backend (pytest) and frontend (node:test + jsdom)
- Rankings count pool wake-ups as a single event
- Older per-file SMB lines are grouped under the share

## 2026-09-29 – 2026-09-30: cause and source
- `spin-who`: fanotify via `fatrace`, source per access (container / Unraid / user)
- Container detection regardless of cgroup namespace; container names via docker.sock
- Source behind `shfs` via inode comparison on `/proc/*/fd`
- Link older lines to containers after the fact on start
- SMB/NFS summarised per share
- Session list: all files per spin session, with time and source
- Events of pool members grouped into one row
- `who.csv` as a full source next to (and later instead of) `activity.csv`
- Labels without the "Unraid:" prefix; the kind is shown through colour

## 2026-09-28 – 2026-09-29: dashboard and collection
- Dashboard with a timeline per disk, histogram per hour, cause ranking and event table
- Live data via an nginx container, refreshed every minute
- `spinmon.sh`: spin state per minute, changes only; inotify watcher for the wake-up and sessions
- Pool detection for the array, XFS/Btrfs and ZFS
- Robust watcher: restarts, cleanup of orphans, clean stop via the process group
- Root folder listings recognised and explained ("Root of pool")
