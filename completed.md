# Completed

Newest first.

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
- Browse through time: per day (24 hours), per week (7 days) and per 30 days, with a "Now" button
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
