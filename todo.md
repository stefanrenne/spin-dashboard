# Todo

Open work, roughly by priority. Move finished items to `completed.md`.

## Bugs
- [ ] "Per day" is far too low for periods that start before the update: `perDay()` in `index.html` divides by the whole period, also the days without `io` lines, so with "30 days" or "All" an SSD seems to write ~30× less and the wear warning never fires. Average from the first `io` line of those drives in the period (key figures, lane, drive page, overview); test with data where only the last day has `io` lines
- [ ] Our own child processes are logged: rule 3 only skips `MY_PID`, so `hdparm`, `smartctl` and `fatrace` (started by the dashboard) showed up as sources on an SSD pool. Remember children of `MY_PID` in `classify_early` while they run and skip them in `handle()`; test with `make_proc(..., ppid=MY_PID)`
- [ ] The `io` buffer is written when *any* HDD spins, not when the HDD that holds `DATA_DIR` spins: with appdata on HDD X and HDD Y spinning, the write still wakes X. Find the device of `DATA_DIR` (mountinfo → source → `disks.ini`) and only flush when it is active or an SSD; unknown keeps today's behaviour. The `who` lines have the same issue; record it in `docs/decisions.md`

## Before the first public release
- [ ] Test on a system with a classic array and parity (`/mnt/diskN`, `/dev/mdXpY`)
- [ ] Test with a ZFS pool with a dataset per share
- [ ] Verify the `shares.ini` field names on Unraid 6.12 (confirmed on 7.3.3: `useCache`, `cachePool`, `cachePool2`), and how a share with secondary storage (`cachePool2` set) should count; Settings › Paths per container shows "unknown" when they do not match
- [ ] Fill in the support link (`<Support>` in the template) once the forum thread exists
- [ ] Support thread on the Unraid forum and submission to Community Applications
- [ ] Version and changelog: no `v*` tag exists yet. A changelog section per version (from `completed.md`) and the version in the page footer and `/health`
- [ ] Check on real hardware that `docker stop` writes the waiting `io` and `wear` lines: `stop()` (SIGTERM) is only unit-tested; make sure `sys.exit` in the signal handler ends `serve_forever` within Docker's 10 seconds

## Security
- [ ] The settings have no authentication: anyone on the LAN can `POST /data/settings.json`, including the webhook URL that the server then POSTs to. Options: an optional token through an environment variable, and/or only allow private addresses for the webhook when explicitly enabled. Document putting the dashboard behind a reverse proxy with authentication in the README

## Watcher and data quality
- [ ] Container names instead of Docker IDs: writes to `docker/containers/<id>/…-json.log` and `docker/overlay2/<layer>/` show hex IDs with `dockerd` as the source. Look up the container through the Docker API (`LogPath`, `GraphDriver.Data.UpperDir`) and name it as the source ("plex, container log")
- [ ] Strip the ` (deleted)` suffix that fatrace adds to paths of files that are already removed
- [ ] `/health` should report per watched filesystem whether its fatrace runs (now only a count; once 7 ran for ~60 mounts unnoticed), and name the fanotify limit (`fs.fanotify.max_user_groups`) when starting one fails
- [ ] Optional, on the own server: clean the noise from 2026-10-20 (some 25,000 opens in `docker/overlay2` and doubled paths from before the watcher fixes); otherwise it ages out after 30 days

## Features
- [ ] Test SAS drives and HBAs; validate the `smartctl -n standby` fallback
- [ ] Bytes per container: only possible for direct `/mnt/diskN` or pool mappings, see `docs/decisions.md`
- [ ] I/O in quiet hours: optionally also notify (webhook) when a pool reads or writes more than N MB inside the quiet hours, not only on a spin-up; useful for SSD pools that never spin down
- [ ] Reads, writes and wear in the CSV export: the `io` lines (per drive, KiB read and written) and the `wear` lines of the period are not exported yet
- [ ] Large downloads with "All": day files are ~6 MB a day on a busy server (mostly `who` lines), so 30 days is ~180 MB. A per-day summary endpoint (like `spins.csv`) for the cards that do not need single lines, or filtering `who` lines on the server by the window
- [ ] Keyboard for SSD lanes: HDD lanes step through segments with the arrow keys, SSD lanes only have an `aria-label`. Arrow keys per bar with the same tooltip
- [ ] SMART wear as a trend on the SSD page: percentage used and written since new over time (from the `wear` lines), with an estimate of when the rated endurance is reached at the current pace
- [ ] Optional wear notification through the webhook: once a day when an SSD is above the limit (now deliberately not sent, see `docs/decisions.md`)
