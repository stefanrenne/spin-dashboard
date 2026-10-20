# Data formats

All data lives in `DATA_DIR/days` (default `/data/days`, on the host usually
`/mnt/user/appdata/spin-dashboard/days`): **one CSV per day**, `YYYY-MM-DD.csv`. The day is the
container's local date (`TZ`, passed in by Unraid). Plain text, no header.

## Lines

Every line starts with epoch seconds and a type, followed by fields per type.

```
1791496800,disk,nvme0n1,cache,ssd
1791496800,disk,sdd,trunk,hdd
1791496800,state,sdd,standby
1791496860,io,nvme0n1,1024,20480,12,310
1791529042,spin,sdd,active
1791529031,proc,16690,Sonarr
1791529032,who,container,sonarr,16690,geopend,/mnt/trunk/Media/series/
1791530200,ctr,radarr,die,0
1791530220,ctr,radarr,start
1791530846,spin,sdd,standby
```

| Type | Fields after `epoch,type` | Meaning |
|---|---|---|
| `disk` | device, name[, kind] | Name from `disks.ini` (`parity`, `disk1`, `trunk`, `trunk2`, `cache`) and `hdd` or `ssd` (from `/sys/block/<dev>/queue/rotational`). Every drive Unraid knows, SSDs and NVMe included. On the first poll of every day and whenever the drives change. Lines without a kind (older versions) are always HDDs: those versions only wrote HDDs. |
| `state` | device, `active`/`standby` | Measured state at that moment, **not a change**: on the first poll of a day and after a start. Never counts as a spin-up. Only for HDDs. |
| `spin` | device, `active`/`standby` | State change between two polls. `active` after `standby` is a spin-up. Only for HDDs. |
| `io` | device, KiB read, KiB written, reads, writes | What the drive did since the previous poll, from the kernel counters in `/sys/block/<dev>/stat` (which never touch the drive). Every drive, SSDs included. See below. |
| `who` | kind, name, pid, action, path | Access to a file or folder (fatrace). See below. |
| `activity` | disk, event, path | Old inotify data (`OPEN+ISDIR`, `MODIFY`, …). Only written by the migration. |
| `proc` | pid, process name | The process behind a container PID, as fatrace names it (`Plex Media Scan`, `Plex Transcoder`; at most 15 characters, commas replaced by spaces). Written right before the first `who` line of that PID and name on a day. See below. |
| `ctr` | name, event[, exit code] | Container lifecycle from Docker's event stream: `start`, `die` (with the exit code when Docker gives one), `healthy`, `unhealthy`. See below. |

Thanks to the `disk` and `state` lines at the top, every day file can be read on its own: the
names and the state at the start of the day are in it, even when the previous day has already
been removed.

### who

| Field | Content |
|---|---|
| kind | `container`, `unraid`, `gebruiker` (user), `onbekend` (unknown) |
| name | Container name, `SMB-share`, `shell (ssh)`, `mover`, `webGUI`, process name, … (commas replaced by spaces). Ends in ` (al gestopt)` ("already stopped") when the process was gone. |
| pid | PID (host namespace) |
| action | `geopend` (opened for reading), `geschreven` (written, or opened with write intent), `aangemaakt` (created), `verwijderd` (deleted), `verplaatst` (moved) |
| path | **Folders end in `/`.** May contain commas; always the last field. |

These values are Dutch and part of the format; the frontend translates them when shown.

**Read vs. write.** The action carries the direction. `geopend` is a read-only open; `geschreven`
comes from a fatrace `W` event (a write to an already-open file) or from an open whose flags
include write intent (fatrace shows `OW`/`ORW`). Create, delete and move are also writes. Older
day files only know `geopend` for every open, so those lines carry no direction: the frontend then
shows no read/write badge. No migration is needed — old and new lines live side by side.

**Deduplication.** The same path + kind + name + action is logged at most once per
`DEDUP_SECONDS` (600). Because the action is part of the key, a read and a write of the same file
within that window each get their own line.

### proc

`who` names the container; `proc` adds which process inside it opened the file, without changing
the `who` line (the path has to stay the last field). The frontend links a `who` line of kind
`container` to the latest `proc` line with the same PID at or before its time. A PID can be reused
after a while, and the same PID can exec another program, so a new name for the same PID gets a new
`proc` line. Only container processes get one: for Unraid and users the name already says it.

### io

One line per drive per poll in which it read or wrote anything: a drive that did nothing (a sleeping HDD)
gets no line. The first poll after a start only reads the counters; a counter that went down (a replaced
device) is skipped and counting starts over.

An SSD is busy every minute, and a line per minute would keep the drive with `DATA_DIR` awake when appdata
lives on an HDD (hard rule 3). So `io` lines wait in memory while every HDD sleeps, and are written as soon
as something else is written (the `disk`/`state` lines of a new day, a spin change) or an HDD spins, and when
the container stops. Until then `/data/days/<day>.csv` appends them to the day file it sends, so the
dashboard is live anyway. Lines that were still waiting are lost when the container is killed instead of
stopped. Lines can therefore be appended out of time order; readers sort by time.

### ctr

One line per Docker event. A `die` directly followed by a `start` (within a minute) is shown as a
restart. The frontend marks these on the source timeline, lists them in the source profile and names
a start, stop or restart in the 5 minutes before a spin-up next to its cause. Only Docker events are
used; health checks (`exec_*`) and other event types are skipped.

## Web

| URL | Content |
|---|---|
| `/data/days.json` | `{"days": ["2026-10-08", "2026-10-09"], "bytes": 13042817, "sizes": [6521400, 6521417]}`: the days in ascending order, the total size and the size per day (uncompressed). The frontend uses them to fetch only the days it shows and to report loading progress. Older versions sent only `days` (and later `bytes`); the frontend then counts finished days instead of bytes. |
| `/data/days/YYYY-MM-DD.csv` | One day file (plus the `io` lines of that day that are still waiting in memory), gzip-compressed when the browser sends `Accept-Encoding: gzip` (about 8× smaller on real data). Each response has an `ETag`; a request with a matching `If-None-Match` gets a `304` without a body. Any other path under `/data/` returns 404. |
| `/data/spins.csv` | Only the `disk`, `state` and `spin` lines of every day file, days in ascending order, same line format. A few KB per day instead of megabytes, so the frontend can compute key figures, the comparison with the previous period and the spin-ups-per-day trend over all days without fetching every day file. Gzip and `ETag`/`304` like the day files; the ETag changes when any day file changes. Older servers return 404; the frontend then uses the days it has loaded. |
| `/data/containers.json` | Per container the bind mounts under `/mnt` and where they live: `{"docker": true, "containers": [{"name": "plex", "state": "running", "project": "", "mounts": [{"source": "/mnt/user/appdata/plex", "target": "/config", "rw": true, "where": {"kind": "share", "share": "appdata", "storage": "pool>array", "pool": "cache", "hdd": true}}]}]}`. `where.kind` is `share` (`/mnt/user/<share>`, with `storage` from Unraid's `shares.ini`: `pool`, `array`, `pool>array` (the mover moves it to the array), `array>pool` or `unknown`), `user` (all of `/mnt/user`), `disk` (`/mnt/diskN`) or `pool` (`/mnt/<pool>`), with `name`. `hdd` is `true`, `false` or `null` (unknown), from the share settings and which disks and pools are rotational, never from looking at the disks. A named local volume that binds a folder (`driver_opts: {type: none, o: bind, device: /mnt/...}`, common with Compose) counts as that folder: `source` is the volume's `device` and the entry gets `"volume": "<name>"`. Other volumes are skipped. `project` is the Docker Compose project label, empty without Compose. Live, not stored; asked from Docker at most every 30 s. Without a Docker socket: `{"docker": false, "containers": []}`. |
| `/data/notify-test` | `POST` (JSON, body ignored) sends a test message to the saved webhook. `200 {"ok": true, "status": 200}`, `400` without a saved webhook, `502` with `{"ok": false, "error": "..."}` when the webhook cannot be reached. It never takes a URL from the request. |
| `/health` | `{"ok": true, "uptime": 560, "poller": {"last_success": 60, "error": null}, "watcher": {"mounts": ["/mnt/trunk"], "fatrace": 1}, "docker": "connected", "threads": {"poller": true, ...}}`, status `200`, or `503` with `"ok": false` when the poller has not succeeded for `3 × POLL_INTERVAL + 60` seconds or a background thread stopped. Missing `fatrace` processes or Docker socket (`"docker": "no socket"`) are reported but keep it healthy, so an auto-heal container does not restart it forever. Used by the Docker `HEALTHCHECK`. |
| `/data/settings.json` | Dashboard settings shared by every browser. `GET` returns `{"hide": ["/mnt/user/appdata/*", "*.log"]}`, or `{}` when nothing has been saved yet (the frontend then moves its old `spindash.hide` list from `localStorage` to the server once). `POST` with `Content-Type: application/json` and a JSON object saves it and returns what was stored. Only known keys are kept (unknown ones are ignored, so older and newer frontends can share the file). `hide`: a list of at most 200 strings of at most 500 characters without control characters; whitespace is trimmed, empty and duplicate entries are dropped. `quiet`, `webhook` and `ssd_watch`: see "Settings file" below. Invalid input returns `400` with `{"error": "..."}`, another content type `415`, a body over 64 KiB `413`. |
| `/data/stream` | Server-Sent Events. Every `who` line is pushed the moment it is logged (`data: <line>`), with a `: ping` every 30 s to keep the connection alive. Powers the live feed; the day files stay the source of truth. |

## CSV export

The CSV button in Events downloads the selected period as `spin-dashboard_YYYYMMDD-HHMM_YYYYMMDD-HHMM.csv`
(UTF-8 with a byte order mark so Excel reads it correctly, CRLF line endings, RFC 4180 quoting). It
follows what the dashboard shows, so hidden paths are left out. Column names and values are English in
every UI language:

| Column | Content |
|---|---|
| `time` | Local time, `YYYY-MM-DD HH:MM:SS` |
| `type` | `spin-up`, `standby`, `state` (measured at the start of a day or after a restart), `access`, `container start`, `container die`, `container healthy`, `container unhealthy` |
| `drive`, `device` | Drive name (`trunk`, `disk1`) and device (`sdd`); for `access` only the drive |
| `source_kind`, `source`, `process` | `container`/`unraid`/`user`/`unknown`, its name, and the process inside a container when known |
| `action` | For `access`: `opened`, `folder opened`, `written`, `created`, `deleted`, `moved`, `share access` |
| `detail` | The path for `access`, `active`/`standby` for `state`, `exit code N` for `container die` |

## Settings file

`DATA_DIR/spindash-settings.json` holds what `/data/settings.json` returns: a JSON object with
`hide` (hidden-path patterns, see `docs/development.md`), `quiet`, `webhook` and `ssd_watch`:

```json
{"hide": ["/mnt/user/appdata/*"],
 "quiet": {"from": "23:00", "to": "07:00", "drives": ["trunk"]},
 "webhook": "https://ntfy.sh/my-topic",
 "ssd_watch": ["cache"]}
```

`ssd_watch` lists the SSD pools (lane keys, like `drives`; `array` for SSDs in the array) whose file access the
watcher also follows with `fatrace`, at most 64; empty or missing means none. HDDs are always followed.

`quiet` is `null` (off) or the window in local time (`from` and `to` as `HH:MM`, different; a window
like 23:00–07:00 runs over midnight) with `drives`: lane keys as the frontend uses them (`array` for
parity and `diskN`, the pool name for a pool and its members), empty for all drives. `webhook` is empty
or an `http(s)` URL of at most 500 characters. When a drive in `drives` spins up inside the window, the
poller POSTs to the webhook (at most once per lane per 10 minutes):
`{"event": "spin-up", "lane": "trunk", "devices": ["sdd", "sde"], "epoch": 1791511200,
"time": "2026-10-09T02:00:00", "text": "...", "content": "..."}`; `text` and `content` hold the same
sentence, so ntfy, Gotify, Slack and Discord work without configuration.

The file is written atomically (via `spindash-settings.json.tmp`) and both names are skipped by the
watcher, like the day files. A missing or unreadable file counts as "nothing saved"; a key with an
invalid value is dropped on reading, the other keys are kept. New keys may be added later; readers
ignore keys they do not know.

## Retention

Every 6 hours, day files older than `RETENTION_DAYS` (default 30) are removed as a whole.

## Migration from the old format

Until October 2026 everything was stored in separate files in `DATA_DIR`:

| File | Line | Becomes |
|---|---|---|
| `spin.csv` | `2026-10-01T04:15:07+02:00,/dev/sdd,active` | `spin` |
| `disks.csv` | `sdd,trunk` | `disk` at the top of every migrated day |
| `who.csv` | `epoch,kind,name,pid,action,path` | `who` |
| `activity.csv` | `epoch,disk,EVENT,path` | `activity` |

`migrate()` runs on every start. If old files are present, they are converted into day files
(with `disk` and `state` lines per day, computed from the spin history), merged with whatever was
already in the day files, and then moved to `DATA_DIR/legacy/`. Running it again is safe: duplicate
lines are merged. Lines that cannot be converted (such as syslog lines in `spin.csv`) are skipped
and counted in the log; they stay in `legacy/`.

## Migrations by change

| Date | Change | Migration |
|---|---|---|
| October 2026 | Separate files (`spin.csv`, `disks.csv`, `who.csv`, `activity.csv`) → day files | `migrate()`, see above. |
| 2026-10-18 | New line types `proc` and `ctr` | None needed: purely additive. Older day files simply have no `proc` or `ctr` lines, and the frontend then shows the container name without a process and no start/stop markers. The history cannot be filled in afterwards: the process names and Docker events of the past were never recorded. Older frontends ignore unknown line types. Tested by `test_old_day_files_without_proc_or_ctr_lines_still_work` and the frontend test for a container without a `proc` line. |
| 2026-10-20 | `disk` lines get a third field `hdd`/`ssd` and also name SSDs; new line type `io` | None needed: purely additive. An older `disk` line has two fields and only ever named an HDD, so a missing kind reads as `hdd`; older frontends take the first two fields and ignore the rest. Older day files have no `io` lines, so the dashboard shows no reads or writes for that time; they cannot be filled in afterwards. Older frontends ignore unknown line types. Tested by `test_old_disk_lines_without_kind_still_load` and the frontend tests that load `disk` lines without a kind. |
| 2026-10-20 | New key `ssd_watch` in `spindash-settings.json` | None needed: purely additive. An older settings file has no `ssd_watch`, which reads as "no SSD pool followed", exactly what older versions did. Older versions ignore the key. Tested by `test_target_mounts_adds_only_the_chosen_ssd_pools`. |
| 2026-10-18 | New keys `quiet` and `webhook` in `spindash-settings.json` | None needed: purely additive. An older settings file has only `hide`, which reads as "no quiet hours, no webhook". Older versions ignore the new keys. Tested by `test_an_invalid_key_in_the_file_does_not_drop_the_others` and the existing settings tests. |
