# Todo

Open work, roughly by priority. Move finished items to `completed.md`.

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
