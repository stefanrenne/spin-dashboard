# Todo

Open work, roughly by priority. Move finished items to `completed.md`.

## Bugs
- [ ] "Per day" is far too low for periods that start before the update: `perDay()` in `index.html` divides by the whole period, also the days without `io` lines, so with "30 days" or "All" an SSD seems to write ~30× less and the wear warning never fires. Average from the first `io` line of those drives in the period (key figures, lane, drive page, overview); test with data where only the last day has `io` lines
- [ ] Our own child processes are logged: rule 3 only skips `MY_PID`, so `hdparm`, `smartctl` and `fatrace` (started by the dashboard) showed up as sources on an SSD pool. Remember children of `MY_PID` in `classify_early` while they run and skip them in `handle()`; test with `make_proc(..., ppid=MY_PID)`
- [ ] The `io` buffer is written when *any* HDD spins, not when the HDD that holds `DATA_DIR` spins: with appdata on HDD X and HDD Y spinning, the write still wakes X. Find the device of `DATA_DIR` (mountinfo → source → `disks.ini`) and only flush when it is active or an SSD; unknown keeps today's behaviour. The `who` lines have the same issue; record it in `docs/decisions.md`
- [ ] Rule 1 risk in `via_user_share()`: it stats `/mnt/user/<share>/<path>` to compare inodes, and shfs may look that path up on other array disks first, which could touch a sleeping disk. Match on the link of `/proc/<pid>/fd/<n>` (the `/mnt/user/...` path as text) and only fall back to inodes when that is ambiguous; verify on a real array with sleeping disks
- [ ] `WATCH` outside `/mnt` no longer works: since the bind-mount fix `target_mounts()` only keeps mounts under `/mnt/`, so the local development example in `docs/development.md` (`WATCH=/tmp`) watches nothing. Apply the `/mnt` limit only to the automatic roots
- [ ] The live stream drops the SSD pools from Reads/Writes per drive: `scheduleLive()` calls `renderActDrives(...bounds(), viewDevs())` without `ssdActDevs()`, so their sections disappear on every streamed event until the next full render
- [ ] A server without HDDs shows "no data": `load()` returns false when there are no `state`/`spin` lines, although the poller now records `io` and `wear` for SSD-only servers. Show the SSD lanes and the wear without spin cards
- [ ] Wrong "written since new" for SATA SSDs that count attribute 241/246 in other units (Intel `Host_Writes_32MiB`, several vendors `Lifetime_Writes_GiB`/`Host_Writes_GiB`): `parse_wear()` always multiplies by the logical block size. Pick the unit from the attribute name in smartctl's JSON; test per vendor
- [ ] `container_name()` caches its fallback (the first 12 hex characters of the ID) when the Docker API does not answer once within 2 s, so that container keeps a hex name until the dashboard restarts. Only cache real names

## Before the first public release
- [ ] Test on a system with a classic array and parity (`/mnt/diskN`, `/dev/mdXpY`)
- [ ] Test with a ZFS pool with a dataset per share
- [ ] Verify the `shares.ini` field names on Unraid 6.12 (confirmed on 7.3.3: `useCache`, `cachePool`, `cachePool2`), and how a share with secondary storage (`cachePool2` set) should count; Settings › Paths per container shows "unknown" when they do not match
- [ ] Fill in the support link (`<Support>` in the template) once the forum thread exists
- [ ] Support thread on the Unraid forum and submission to Community Applications
- [ ] Version and changelog: no `v*` tag exists yet. A changelog section per version (from `completed.md`) and the version in the page footer and `/health`

## Security
- [ ] The settings have no authentication: anyone on the LAN can `POST /data/settings.json`, including the webhook URL that the server then POSTs to. Options: an optional token through an environment variable, and/or only allow private addresses for the webhook when explicitly enabled. Document putting the dashboard behind a reverse proxy with authentication in the README
- [ ] `GET /data/settings.json` returns the webhook URL, which often holds a secret (an ntfy topic, a Discord or Slack token). Return only whether one is set, or a masked form, and keep the full URL on the server
- [ ] The whole dashboard (every file path, container and share name) is readable without authentication. Say so in the README and the CA template, and advise against exposing it to the internet without a login in front

## Performance
- [ ] Today's day file is downloaded in full every minute: the waiting `io` lines change its ETag every poll, so the browser fetches it again (around 1 MB gzipped on a busy day) and `rebuild()` parses every loaded day again, 30 days with "All". Fetch only the bytes added since the last fetch (a `Range` request or `?from=`) and parse only the new lines
- [ ] Large downloads with "All": day files are ~6 MB a day on a busy server (mostly `who` lines), so 30 days is ~180 MB. A per-day summary endpoint (like `spins.csv`) for the cards that do not need single lines, or filtering `who` lines on the server by the window
- [ ] `/data/spins.csv` and `/data/days/<today>.csv` read and gzip today's whole file on every request while `io` lines wait in memory (the caches are bypassed). Cache the file part and only add the waiting lines
- [ ] File access on an SSD pool fills the day files with reads that matter little: with `frunk` watched a day file grew from ~0.4 MB to ~2.6 MB in 9 hours, mostly opens of program files (scrypted's Python plugins, Plex). A read from an SSD wakes nothing and does not wear it: a "writes only" option per SSD pool, on by default
- [ ] `write_who()`: when `recent` holds more than 50,000 keys that are all younger than `DEDUP_SECONDS` (a big scan), every new line walks the whole dict under `write_lock`. Evict in time order (`OrderedDict`) instead
- [ ] The poller asks the drives one after another (`hdparm` with a 15 s timeout each) and then sleeps `POLL_INTERVAL`, so one slow or hanging drive delays all others and the poll times drift. Ask the drives in parallel and keep a fixed rate

## Watcher and data quality
- [ ] Container names instead of Docker IDs: writes to `docker/containers/<id>/…-json.log` and `docker/overlay2/<layer>/` show hex IDs with `dockerd` as the source. Look up the container through the Docker API (`LogPath`, `GraphDriver.Data.UpperDir`) and name it as the source ("plex, container log")
- [ ] Strip the ` (deleted)` suffix that fatrace adds to paths of files that are already removed
- [ ] `/health` should report per watched filesystem whether its fatrace runs (now only a count; once 7 ran for ~60 mounts unnoticed), and name the fanotify limit (`fs.fanotify.max_user_groups`) when starting one fails
- [ ] Docker events: after a reconnect the events of the gap are lost, exactly when Docker restarts and containers stop and start. Reconnect with `/events?since=<time of the last event>`. Also log `oom` (a container killed for lack of memory) as a `ctr` event
- [ ] Optional, on the own server: clean the noise from 2026-10-20 (12.9 MB instead of the usual 0.3–0.5 MB: some 25,000 opens in `docker/overlay2` and doubled paths from before the watcher fixes); otherwise it ages out after 30 days

## Features
- [ ] Unassigned Devices: HDDs mounted by the Unassigned Devices plugin (`/mnt/disks/<name>`) are not in `disks.ini`, so they are neither polled nor watched, and Settings › Paths per container shows `/mnt/disks` and `/mnt/remotes` as a pool called "disks" or "remotes". Find where the plugin keeps device and mount point (without touching the drives) and treat them like pools; network mounts under `/mnt/remotes` stay out
- [ ] Test SAS drives and HBAs; validate the `smartctl -n standby` fallback
- [ ] Bytes per container: only possible for direct `/mnt/diskN` or pool mappings, see `docs/decisions.md`
- [ ] I/O in quiet hours: optionally also notify (webhook) when a pool reads or writes more than N MB inside the quiet hours, not only on a spin-up; useful for SSD pools that never spin down
- [ ] Reads, writes and wear in the CSV export: the `io` lines (per drive, KiB read and written) and the `wear` lines of the period are not exported yet
- [ ] Keyboard for SSD lanes: HDD lanes step through segments with the arrow keys, SSD lanes only have an `aria-label`. Arrow keys per bar with the same tooltip
- [ ] SMART wear as a trend on the SSD page: percentage used and written since new over time (from the `wear` lines), with an estimate of when the rated endurance is reached at the current pace
- [ ] Optional wear notification through the webhook: once a day when an SSD is above the limit (now deliberately not sent, see `docs/decisions.md`)
- [ ] Refresh right away when a hidden tab becomes visible again (`visibilitychange`); now the data can be up to a minute old

## Development
- [ ] `docs/architecture.md`: the diagram still has Dutch text ("elke who-regel live", "spin-regels van alle dagen", "instellingen"), against the English-only convention, and `spins.csv` now carries more than spin lines
- [ ] README › Permissions: the Docker socket is described as "only to translate container IDs into names", but it also feeds the start/stop/health events and the mounts and volumes per container; SMART on SSDs is not mentioned. `DEDUP_SECONDS` is in neither the README nor the CA template
- [ ] `package-lock.json` is in `.gitignore` and CI runs `npm install`, so any run can pick up a newer jsdom and break. Commit the lock file and use `npm ci`
- [ ] Dependabot for GitHub Actions, npm and the Docker base image, and a weekly scheduled image build, so Debian security fixes (fatrace, smartmontools, the Python base) reach `latest` without a code change
- [ ] Test gaps: the `watcher()` loop (restarting on a mount change or a dead fatrace; split the loop body into a testable function), `stream_events()` (SSE) and `fd_snapshot()`
- [ ] The frontend tests take ~50 s because every `load()` waits a fixed 300 ms; wait for a "ready" signal from the page instead
