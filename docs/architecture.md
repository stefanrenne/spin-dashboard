# Architecture

```
┌──────────────────────── container (privileged, --pid=host) ────────────────────────┐
│                                                                                    │
│  poller ── hdparm -C / smartctl -n standby ──► days/<day>.csv  (spin, state)       │
│     ├── /emhttp/disks.ini + /sys/block/*/rotational ──► days/<day>.csv  (disk)     │
│     ├── /sys/block/*/stat (every drive, SSDs too) ──► io_pending ──► (io)          │
│     ├── smartctl -n standby -j -A (SSDs only, hourly) ──► io_pending ──► (wear)    │
│     └── io_pending is written while the drive of DATA_DIR spins or is an SSD       │
│                                                                                    │
│  watcher ── fatrace -c per filesystem ──► queue ──► handle() ──► days/<day>.csv    │
│             (HDD mounts, + SSD pools in ssd_watch; own PIDs and Docker's storage   │
│              skipped)                                                              │
│                                         classify(pid): /proc/<pid>/cgroup, stat    │
│                                         container_name(): docker.sock              │
│                                         shfs → via_user_share(): /proc/*/fd,       │
│                                           only when no sleeping drive is touched   │
│                                                                                    │
│  docker ── /events (start, die, health) ──► days/<day>.csv  (ctr)                  │
│     └── /containers/json + /emhttp/shares.ini ──► /data/containers.json            │
│                                                                                    │
│  housekeeping ── prune_days() every 6 hours; migrate() and relabel_existing()      │
│                  on start                                                          │
│                                                                                    │
│  web ── ThreadingHTTPServer :8089 ── /  → static/index.html                        │
│                                      /data/days.json, /data/days/<day>.csv         │
│                                      /data/stream  (SSE: every who line, live)     │
│                                      /data/spins.csv (summary of all days)         │
│                                      /data/containers.json (mounts per container)  │
│                                      /data/settings.json (GET/POST: settings)      │
│                                      /data/notify-test (POST: test the webhook)    │
│                                      /health (poller, watcher, docker)             │
└────────────────────────────────────────────────────────────────────────────────────┘
          ▲ /mnt (ro,slave)   ▲ /var/local/emhttp (ro)   ▲ docker.sock (ro)   ▼ /data (rw)
```

## Components

**Poller.** Every `POLL_INTERVAL` seconds, on a fixed rate (`next_poll()`: a slow poll does not shift the
next one), asking all HDDs at the same time so one slow or hanging drive does not hold up the others. A spin-up inside the quiet hours (Settings) of a drive that
takes part is sent to the webhook, in a separate thread so a slow receiver never delays the poll. Determines every drive from `disks.ini` and whether it
spins (`rotational`); only HDDs are asked their state, and changes become `spin` lines. On the first
poll after start and on the first poll of each day it writes the current state of every HDD as `state`
lines, plus the name and kind (`hdd`/`ssd`) of every drive as `disk` lines. It also reads the kernel's
I/O counters of every drive (`/sys/block/<dev>/stat`, which never wakes a drive) and turns the
difference with the previous poll into an `io` line. Those wait in memory (`io_pending`) while the drive
that holds `DATA_DIR` sleeps (`data_dir_devices()`; unknown: while every HDD sleeps), so a busy SSD does
not make the dashboard wake that drive every minute; the web server adds the
waiting lines to the day file it sends. See `io` in `data-formats.md`. Once an hour (and on the first poll
of a day) it asks every SSD, never an HDD, for its SMART wear (`wear` lines), which wait in the same way.

The poller keeps what it last saw (`drive_states`, `known_devices`) for the watcher, so the watcher never
has to ask a drive itself. Our own tools (`hdparm`, `smartctl`, `fatrace`) run through `run_own()`, which
registers their PIDs so the watcher never logs them (rule 3).

**Watcher.** Determines the mounts to watch: `/mnt/<name>` for array disks (`disk1`) and
pools (pool member `trunk2` belongs to `/mnt/trunk`), plus every mount below them (ZFS datasets).
SSD pools are only watched when they are chosen in Settings (`ssd_watch`): an appdata or cache pool
gives a lot of events. Of those only writes are logged unless reads are chosen too (`ssd_reads`): a read
from an SSD wakes nothing and does not wear it, and the opens of program files would fill the day files. One `fatrace` per filesystem: a bind mount of a filesystem that is already watched
(a Docker volume that binds a folder of the pool) would report every event a second time under another
path, so of the mounts of one source only the shortest under `/mnt` is used. Overlay (container root
filesystems, when Docker's data-root is on a pool), tmpfs and other non-drive filesystems are skipped.
Because fatrace marks the whole filesystem, Docker's own storage on a watched pool still shows up through
its real paths: every program a container starts opens its binaries in `docker/overlay2/<layer>/`. Opens
in Docker's storage are therefore not logged, a write to a layer is logged as that layer's folder (one
line instead of thousands on an image pull), and writes in `docker/containers/<id>/` (the container
logs) as they are. Each mount gets a `fatrace -c` with filter `O+WD<` (open, write, create, delete, move). `fatrace` uses
`FAN_MARK_FILESYSTEM`, so access from other mount namespaces (the host, other containers) is
seen as well. Every minute it checks whether the mounts changed or a `fatrace` stopped.

**Classification.** Processes in `IGNORE_PROCS` (default `cache_dirs`), or with such a process as
an ancestor, are not logged. After that the order is: container (cgroup) → stopped process →
SMB/NFS → shell (sshd, ttyd, login) → shfs → btrfs → mover → cron → webGUI → process name. Opens by
`shfs` are re-examined 0.4 s later: which other process has the same file open via `/mnt/user`
(inode comparison)? Only when that lookup through `/mnt/user` cannot touch a sleeping drive
(`user_share_lookup_safe()`, see `decisions.md`); otherwise the access is logged as `shfs (via /mnt/user)`. The source of a new PID is already determined in the reader thread
(`classify_early`) and remembered per PID for `PID_TTL` (30 s), so events of a process that has
stopped in the meantime get the same source; "stopped" itself is not remembered. See
*Ignoring Cache Dirs* in `decisions.md`.

**Deduplication.** The same path + kind + name + action only once per `DEDUP_SECONDS` (600).
SMB/NFS is summarised into one line per share. The action is part of the key so that a read and a
write of the same file within the window each appear once.

**Storage.** All lines go to the day file of their own timestamp (`append_lines`). Old day files
are removed as a whole. On start, `migrate()` converts files from older versions; see
`docs/data-formats.md`.

**Frontend.** It fetches `days.json` and only the day files the shown period needs: the window
plus one day on each side (a cause just after midnight, a session that ends the next day), and
always today and yesterday for the live feed. Fetched days stay in memory; browsing back or
choosing "All" fetches the missing days on demand. A day is parsed once and kept as parsed lines; every
minute it refetches `days.json`, only the bytes added to the live days since the last fetch (`?from=`, see
`data-formats.md`, parsing only those) and the waiting `io`/`wear` lines (`/data/pending.csv`), and then
merges the parsed parts into one data set. It merges `activity` lines (old inotify data) and `who` lines into one activity stream
and links it to spin-ups. `state` lines set the state but are never a spin-up. The period is
last hour, 24 hours (the default), 7 days, 30 days or all; `offset` shifts the window back by whole
periods. Every switch shows the skeleton and the loading bar while missing days are fetched and the
data is processed; a newer click overtakes an older one, and the minute refresh waits until a switch
is done.
Texts come from `I18N` (en, nl, fr, de, es) via `t()`; dates via `Intl` in the locale of the chosen
language. The `(al gestopt)` suffix in who lines is translated when shown; the data format itself
stays unchanged.

*Timeline per drive* is one card per pool and one for the array (`lanes:<key>` in the card layout,
made from `#laneTpl` when the pool shows up in the data). An HDD lane draws spinning and standby with the
data read and written as a thin layer below it; an SSD lane (from the `ssd` kind in the `disk` lines) only
draws reads and writes and what it wrote per day, and SSDs never count in the spin figures, quiet hours or causes.
*Key figures* get a fifth tile with what all SSDs write per day, compared with the previous period from the hourly
`io` totals in `spins.csv`. One SSD that writes more than the limit in Settings (`wear_warn`, default 100 GB per
day) is marked on the tile, its lane, the overview and its page; the pages also show the SMART wear. The drive pages
of the overview show the same reads and writes for every pool and drive.

At the top, *Key figures* show spin-ups, spinning hours, the share of time all drives slept and the
longest full rest, each compared with the previous period of the same length. *Spin-ups per day* shows
the trend over all days with the selected period highlighted, and *Spin-ups per hour of day* covers at least the
last 7 days. *Activity per source* ranks containers and processes by the spin-ups they caused, with their reads,
writes and drives. *Timeline per source* draws every access of the busiest sources on the same axis, over a band that
shows when any drive spins; *Kept awake per source* adds up per source how long its accesses postponed
the spin-down; *Rhythm per source* recognises fixed intervals and daily times. Clicking a source name opens its
profile. *Spin time and rest per drive* shows per lane the median run after a spin-up, the longest
rest and how often a drive woke up within 30 minutes of going to standby. Both use `/data/spins.csv`: only the
spin lines of every day, so the comparison needs no extra day files.

*Paths per container* in Settings comes from `/data/containers.json`: the bind mounts of every
container (Docker API) and, for `/mnt/user/<share>`, where the share lives according to Unraid's
`shares.ini`. Whether a path is on an HDD follows from those settings and which disks and pools are
rotational, never from looking at the disks (that would wake them). The `docker` thread also
follows Docker's event stream and writes `ctr` lines for start, die and health; container processes
get a `proc` line with their fatrace name, so the feed and the profile can say "plex › Plex Media Scan".

Below the spin-up analysis, *Reads per drive* and *Writes per drive* show per drive the top
sources and files in that direction over the period (also without spin-ups), and a *Live activity* feed lists the most recent file
events newest-first with an R/W marker and a relative time. The feed is **live**: the web server keeps
a Server-Sent Events stream (`/data/stream`) open per browser, and `write_who()` pushes each new line
onto every open stream the moment it is logged. So the feed updates instantly instead of waiting for
the next minute fetch; the periodic fetch stays the source of truth and re-syncs on (re)load. New
items get a short highlight, the feed re-renders every 30 s so the "x min ago" labels stay current,
and hovering a truncated file path shows the full path in an overlay.

## Why one process

This used to be a User Script (`spinmon.sh`), an inotify watcher, nginx and a separate
`spin-who` container. One process with threads makes installation through Community Applications
possible and prevents parts from stopping independently of each other.
