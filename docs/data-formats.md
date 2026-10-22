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
1791496860,wear,nvme0n1,3,42949672960
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
| `wear` | device, percentage used, KiB written | SMART wear of an SSD: how much of its rated endurance is used (may exceed 100) and everything written since it was new. Either field may be empty when the drive does not report it. Only SSDs, once an hour and on the first poll of a day. See below. |
| `who` | kind, name, pid, action, path | Access to a file or folder (fatrace). See below. |
| `activity` | disk, event, path | Old inotify data (`OPEN+ISDIR`, `MODIFY`, …). Only written by the migration. |
| `proc` | pid, process name | The process behind a container PID, as fatrace names it (`Plex Media Scan`, `Plex Transcoder`; at most 15 characters, commas replaced by spaces). Written right before the first `who` line of that PID and name on a day. See below. |
| `cio` | container, device, KiB read, KiB written | What a container read and wrote on one drive since the previous `cio` line, from `io.stat` of its cgroup; written every 10 minutes, only when not zero. See below. |
| `ctr` | name, event[, exit code] | Container lifecycle from Docker's event stream: `start`, `die` (with the exit code when Docker gives one), `healthy`, `unhealthy`, `oom` (killed for lack of memory, followed by a `die`). See below. |

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
lives on an HDD (hard rule 3). So `io` lines wait in memory while the drive that holds `DATA_DIR` sleeps,
and are written as soon as something else is written (the `disk`/`state` lines of a new day, a spin change),
that drive spins or is an SSD, and when the container stops. The poller finds that drive in
`/proc/self/mountinfo` (a ZFS pool, an array disk, a pool partition, or a user share that only lives on one
pool); when it cannot tell, the lines are written while any HDD spins. Until then `/data/days/<day>.csv` appends them to the day file it sends, so the
dashboard is live anyway. Lines that were still waiting are lost when the container is killed instead of
stopped. Lines can therefore be appended out of time order; readers sort by time.

### wear

From `smartctl -n standby -j -A /dev/<dev>`, only ever for drives whose `disk` line says `ssd` (never on an HDD,
hard rule 1); `-n standby` is a second guard. NVMe: `percentage_used` and `data_units_written` (units of
512 000 bytes). SATA: the life left from the first known attribute of 177 (Samsung), 231, 233 (Intel), 202
(Crucial/Micron) and 169 (WD), as 100 minus its normalised value, and the written blocks from 241 or 246 times
the logical block size. A drive that reports neither gets no line. Wear lines wait in memory like `io` lines.

### cio

The poller reads `io.stat` of every container cgroup of the host (`/proc/1/root/sys/fs/cgroup`, `docker/<id>`
or `system.slice/docker-<id>.scope`), maps major:minor to the drives it knows and adds the increases up per
container and drive; every 10 minutes the totals become `cio` lines (container name as Docker names it), which
wait in memory like `io` lines. The first poll after a start only takes the counters. Only what the container
itself reads and writes counts: access through `/mnt/user` is done by `shfs` (see `decisions.md`).

### Unassigned Devices

A drive mounted by the Unassigned Devices plugin at `/mnt/disks/<label>` (found in the mount table, never by
touching the drive) gets the name `disks/<label>` in `disk` lines and is its own lane (`disks/<label>` as lane
key, also in `quiet.drives` and `ssd_watch`); its spin state and file access are followed like those of a pool.
Network shares under `/mnt/remotes` are no drives: `containers.json` gives them `"kind": "remote"`.

### ctr

One line per Docker event. A `die` directly followed by a `start` (within a minute) is shown as a
restart. The frontend marks these on the source timeline, lists them in the source profile and names
a start, stop or restart in the 5 minutes before a spin-up next to its cause. Only Docker events are
used; health checks (`exec_*`) and other event types are skipped. After a reconnect to Docker's event
stream the server asks for the events since the last one it saw (`since`), so a Docker restart leaves no gap.

## Web

| URL | Content |
|---|---|
| `/data/days.json` | `{"days": ["2026-10-08", "2026-10-09"], "bytes": 13042817, "sizes": [6521400, 6521417]}`: the days in ascending order, the total size and the size per day (uncompressed). The frontend uses them to fetch only the days it shows and to report loading progress. Older versions sent only `days` (and later `bytes`); the frontend then counts finished days instead of bytes. |
| `/data/days/YYYY-MM-DD.csv` | One day file (plus the `io` lines of that day that are still waiting in memory), gzip-compressed when the browser sends `Accept-Encoding: gzip` (about 8× smaller on real data). Each response has an `ETag`; a request with a matching `If-None-Match` gets a `304` without a body. Any other path under `/data/` returns 404. |
| `/data/days/YYYY-MM-DD.csv?from=N&file=ID` | Only the bytes of the day file from byte `N` on, up to its last whole line, without the waiting lines. `X-From` says where the part starts: `N`, or `0` when the file was rewritten since (another `file` ID than `X-File`, or shorter than `N`); `X-Size` where it ends and `X-File` the ID of the file, for the next request. Gzip when accepted (above 1 KB). The frontend fetches a day once from `from=0` and the live days then only for what was added. Older servers ignore `from` and send the whole file. |
| `/data/pending.csv` | The `io` and `wear` lines that wait in memory (see `io` above), of every day. `ETag`/`304`. Older servers return 404 (they add the waiting lines to the day file). |
| `/data/spins.csv` | A summary of every day file, days in ascending order: the `disk`, `state`, `spin` and `wear` lines as they are, plus the `io` lines added up per drive per hour (one line per drive per hour, at the time of the last line it covers; fields as in `io`). The `io` and `wear` lines that still wait in memory are included. A few KB per day instead of megabytes, so the frontend can compute key figures, the comparison with the previous period and the spin-ups-per-day trend over all days without fetching every day file. Gzip and `ETag`/`304` like the day files; the ETag changes when any day file or the waiting lines change. Older servers return only the spin lines, or 404; the frontend then uses the days it has loaded. |
| `/data/containers.json` | Per container the bind mounts under `/mnt` and where they live: `{"docker": true, "containers": [{"name": "plex", "state": "running", "project": "", "mounts": [{"source": "/mnt/user/appdata/plex", "target": "/config", "rw": true, "where": {"kind": "share", "share": "appdata", "storage": "pool>array", "pool": "cache", "hdd": true}}]}]}`. `where.kind` is `share` (`/mnt/user/<share>`, with `storage` from Unraid's `shares.ini`: `pool`, `array`, `pool>array` (the mover moves it to the array), `array>pool`, `pool>pool2` / `pool2>pool` (Unraid 7: a second pool, `cachePool2`, as secondary storage instead of the array; the entry then also has `pool2`) or `unknown`; Unraid 6.12 and 7.x name the fields `useCache`, `cachePool` and, from 7.0, `cachePool2`), `user` (all of `/mnt/user`), `disk` (`/mnt/diskN`) or `pool` (`/mnt/<pool>`), with `name`. `hdd` is `true`, `false` or `null` (unknown), from the share settings and which disks and pools are rotational, never from looking at the disks. A named local volume that binds a folder (`driver_opts: {type: none, o: bind, device: /mnt/...}`, common with Compose) counts as that folder: `source` is the volume's `device` and the entry gets `"volume": "<name>"`. Other volumes are skipped. `project` is the Docker Compose project label, empty without Compose. `id` is the full container ID, so the frontend can show the name for lines logged with the short ID (a container whose name Docker did not give yet, right after it was created or updated). `icon` is `data/icons/<name>.png` when the container has an icon: a file from Unraid (`<name>-icon.png` in `/emhttp/plugins/dynamix.docker.manager/images`, tmpfs) or an http(s) URL in its `net.unraid.docker.icon` label; otherwise empty. Older servers do not send it. Live, not stored; asked from Docker at most every 30 s. Without a Docker socket: `{"docker": false, "containers": []}`. |
| `/data/icons/<name>.png` | The icon of a container: the file Unraid keeps (`/emhttp/plugins/dynamix.docker.manager/images/<name>-icon.png`), otherwise the image its `net.unraid.docker.icon` label points to, fetched by the server (http(s) only, kept in memory, a failure tried again after 10 minutes). So the page loads no images from other sites. The type comes from the content (PNG, JPEG, GIF, WebP or SVG: Unraid also stores SVGs as `.png`); with `Cache-Control: max-age=86400`, `X-Content-Type-Options: nosniff` and `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox`, so an SVG opened directly runs nothing. `404` for a container without an icon, content that is no image, a name that is not a plain file name (letters, digits, `_`, `.`, `-`, not starting with a dot) or more than 1 MiB. |
| `/data/notify-test` | `POST` (JSON, body ignored) sends a test message to the saved webhook. `200 {"ok": true, "status": 200}`, `400` without a saved webhook, `502` with `{"ok": false, "error": "..."}` when the webhook cannot be reached. It never takes a URL from the request. With `SETTINGS_TOKEN` set it needs the token like a save (`401` without it). |
| `/health` | `{"ok": true, "version": "0.9.0", "uptime": 560, "poller": {"last_success": 60, "error": null}, "watcher": {"mounts": ["/mnt/trunk"], "fatrace": 1, "running": {"/mnt/trunk": true}}, "docker": "connected", "threads": {"poller": true, ...}}`, status `200`, or `503` with `"ok": false` when the poller has not succeeded for `3 × POLL_INTERVAL + 60` seconds or a background thread stopped. Missing `fatrace` processes or Docker socket (`"docker": "no socket"`) are reported but keep it healthy, so an auto-heal container does not restart it forever. Used by the Docker `HEALTHCHECK`. |
| `/data/settings.json` | Dashboard settings shared by every browser. `GET` returns `{"hide": ["/mnt/user/appdata/*", "*.log"]}`, or `{}` when nothing has been saved yet (the frontend then moves its old `spindash.hide` list from `localStorage` to the server once). `POST` with `Content-Type: application/json` and a JSON object saves it and returns what was stored. Only known keys are kept (unknown ones are ignored, so older and newer frontends can share the file). `hide`: a list of at most 200 strings of at most 500 characters without control characters; whitespace is trimmed, empty and duplicate entries are dropped. `quiet`, `webhook`, `ssd_watch`, `ssd_reads`, `wear_warn` and `wear_notify`: see "Settings file" below. Invalid input returns `400` with `{"error": "..."}`, another content type `415`, a body over 64 KiB `413`. The webhook is returned masked (`https://host/…`: scheme and host only, the rest often holds a secret); a save with exactly that masked value keeps the stored URL. With the environment variable `SETTINGS_TOKEN` set, the answer has `"locked": true` and a save needs the token in the `X-Settings-Token` header (`401 {"error": "settings token required"}` without it or with a wrong one). |
| `/data/stream` | Server-Sent Events. Every `who` line is pushed the moment it is logged (`data: <line>`), with a `: ping` every 30 s to keep the connection alive. Powers the live feed; the day files stay the source of truth. |

## CSV export

The CSV button in Events downloads the selected period as `spin-dashboard_YYYYMMDD-HHMM_YYYYMMDD-HHMM.csv`
(UTF-8 with a byte order mark so Excel reads it correctly, CRLF line endings, RFC 4180 quoting). It
follows what the dashboard shows, so hidden paths are left out. Column names and values are English in
every UI language:

| Column | Content |
|---|---|
| `time` | Local time, `YYYY-MM-DD HH:MM:SS` |
| `type` | `spin-up`, `standby`, `state` (measured at the start of a day or after a restart), `access`, `container start`, `container die`, `container healthy`, `container unhealthy`, `container oom`, `io` (reads and writes of a drive, added up per hour, at the time of the last poll in it), `wear` (SMART) |
| `drive`, `device` | Drive name (`trunk`, `disk1`) and device (`sdd`); for `access` only the drive |
| `source_kind`, `source`, `process` | `container`/`unraid`/`user`/`unknown`, its name, and the process inside a container when known |
| `action` | For `access`: `opened`, `folder opened`, `written`, `created`, `deleted`, `moved`, `share access` |
| `detail` | The path for `access`, `active`/`standby` for `state`, `exit code N` for `container die` |
| `read_kib`, `written_kib` | For `io`: KiB read and written by that drive in that hour |
| `wear_pct`, `written_since_new_kib` | For `wear`: SMART percentage used and KiB written since new (empty when the drive does not report it) |

The last four columns were added on 2026-10-21; they are empty for the other types, and the first nine are
unchanged, so a script that reads columns by name keeps working.

## Settings file

`DATA_DIR/spindash-settings.json` holds what `/data/settings.json` returns: a JSON object with
`hide` (hidden-path patterns, see `docs/development.md`), `quiet`, `webhook`, `ssd_watch`, `ssd_reads`, `wear_warn` and `wear_notify`:

```json
{"hide": ["/mnt/user/appdata/*"],
 "quiet": {"from": "23:00", "to": "07:00", "drives": ["trunk"]},
 "webhook": "https://ntfy.sh/my-topic",
 "ssd_watch": ["cache"],
 "ssd_reads": [],
 "wear_warn": 100}
```

`ssd_watch` lists the SSD pools (lane keys, like `drives`; `array` for SSDs in the array) whose file access the
watcher also follows with `fatrace`, at most 64; empty or missing means none. HDDs are always followed.
`ssd_reads` lists the pools of `ssd_watch` whose reads are logged too; for the others only writes (create,
write, delete, move) are logged, because a read from an SSD wakes nothing and does not wear it. Empty or
missing: only writes for every SSD pool.

`wear_warn` is the number of GB (GiB, as the dashboard shows them) one SSD may write per day before the dashboard
marks it, 1 to 1 000 000; `null` means never warn, and a missing key means the default of 100. Only the frontend
uses it.

`quiet` is `null` (off) or the window in local time (`from` and `to` as `HH:MM`, different; a window
like 23:00–07:00 runs over midnight) with `drives`: lane keys as the frontend uses them (`array` for
parity and `diskN`, the pool name for a pool and its members), empty for all drives. `webhook` is empty
or an `http(s)` URL of at most 500 characters. When a drive in `drives` spins up inside the window, the
poller POSTs to the webhook (at most once per lane per 10 minutes):
`{"event": "spin-up", "lane": "trunk", "devices": ["sdd", "sde"], "epoch": 1791511200,
"time": "2026-10-09T02:00:00", "text": "...", "content": "..."}`; `text` and `content` hold the same
sentence, so ntfy, Gotify, Slack and Discord work without configuration.

`quiet.io_mb` (optional, 1 to 10 000 000) also sends a message when a lane in `drives` (an SSD pool can be in
it too) reads and writes more than that many MB inside one quiet-hours window, once per lane per window:
`{"event": "io", "lane": "cache", "kib": 112640, "epoch": ..., "time": "...", "text": "...", "content": "..."}`.

`wear_notify` (`true`/`false`, default off) sends a message when an SSD writes more than `wear_warn` GB in one
day, at most once per SSD per day: `{"event": "wear", "device": "nvme0n1", "name": "cache", "kib": ...,
"limit_gb": 100, "epoch": ..., "time": "...", "text": "...", "content": "..."}`.

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
| 2026-10-20 | New line type `wear`; `/data/spins.csv` also carries `wear` lines and hourly `io` totals | None needed: purely additive. Older day files have no `wear` lines, so the dashboard shows no SMART wear for that time; it cannot be filled in afterwards. `spins.csv` is computed from the day files on every request, nothing stored changes. Older frontends ignore the unknown line types. Tested by `test_spins_adds_up_io_per_drive_per_hour_and_keeps_wear` and `test_old_disk_lines_without_kind_still_load`. |
| 2026-10-20 | New key `wear_warn` in `spindash-settings.json` | None needed: purely additive. An older settings file has no `wear_warn`, which reads as the default of 100 GB per day. Older versions ignore the key. Tested by `test_wear_warn_setting_is_checked`. |
| 2026-10-21 | New line type `cio`; drives of Unassigned Devices in `disk` lines as `disks/<label>`; `containers.json` kind `remote` | None needed: purely additive. Older day files have no `cio` lines and no Unassigned Devices drives (they were not followed), so those simply show nothing for that time. Older frontends ignore `cio` and show a `disks/<label>` drive as a pool of that name. Lane keys with `disks/` are new values in `quiet.drives` and `ssd_watch`; older settings files have none. Tested by `test_unassigned_devices_are_found_from_the_mounts`, `test_bytes_per_container_are_added_up_and_written_every_ten_minutes` and the frontend tests for both. |
| 2026-10-21 | New optional field `io_mb` in `quiet`, new key `wear_notify` in `spindash-settings.json` | None needed: purely additive. An older settings file has neither, which reads as "no I/O message, no wear message", what older versions did. Older versions drop the unknown field (they rebuild `quiet` from `from`, `to` and `drives`) and ignore the key. Tested by `test_quiet_io_mb_is_checked` and the notification tests. |
| 2026-10-21 | New `ctr` event `oom` | None needed: purely additive, a new value in an existing field. Older day files simply have none; older frontends show the event without a translated label. Tested by `test_ctr_line_for_an_oom_kill` and the frontend test for an OOM kill. |
| 2026-10-21 | New key `ssd_reads` in `spindash-settings.json` | None needed: purely additive. An older settings file has no `ssd_reads`, which reads as "only writes on every SSD pool". That is a deliberate change of what is recorded from now on: reads on SSD pools were logged before and are not anymore unless the pool is chosen; lines already in the day files stay. Older versions ignore the key. Tested by `test_ssd_pools_log_only_writes_unless_reads_are_chosen` and `test_ssd_reads_setting_is_checked`. |
| 2026-10-20 | New key `ssd_watch` in `spindash-settings.json` | None needed: purely additive. An older settings file has no `ssd_watch`, which reads as "no SSD pool followed", exactly what older versions did. Older versions ignore the key. Tested by `test_target_mounts_adds_only_the_chosen_ssd_pools`. |
| 2026-10-18 | New keys `quiet` and `webhook` in `spindash-settings.json` | None needed: purely additive. An older settings file has only `hide`, which reads as "no quiet hours, no webhook". Older versions ignore the new keys. Tested by `test_an_invalid_key_in_the_file_does_not_drop_the_others` and the existing settings tests. |
