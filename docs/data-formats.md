# Data formats

All data lives in `DATA_DIR/days` (default `/data/days`, on the host usually
`/mnt/user/appdata/spin-dashboard/days`): **one CSV per day**, `YYYY-MM-DD.csv`. The day is the
container's local date (`TZ`, passed in by Unraid). Plain text, no header.

## Lines

Every line starts with epoch seconds and a type, followed by fields per type.

```
1791496800,disk,sdd,trunk
1791496800,state,sdd,standby
1791529042,spin,sdd,active
1791529032,who,container,sonarr,16690,geopend,/mnt/trunk/Media/series/
1791530846,spin,sdd,standby
```

| Type | Fields after `epoch,type` | Meaning |
|---|---|---|
| `disk` | device, name | Name from `disks.ini` (`parity`, `disk1`, `trunk`, `trunk2`). On the first poll of every day and whenever the names change. |
| `state` | device, `active`/`standby` | Measured state at that moment, **not a change**: on the first poll of a day and after a start. Never counts as a spin-up. |
| `spin` | device, `active`/`standby` | State change between two polls. `active` after `standby` is a spin-up. |
| `who` | kind, name, pid, action, path | Access to a file or folder (fatrace). See below. |
| `activity` | disk, event, path | Old inotify data (`OPEN+ISDIR`, `MODIFY`, …). Only written by the migration. |

Thanks to the `disk` and `state` lines at the top, every day file can be read on its own: the
names and the state at the start of the day are in it, even when the previous day has already
been removed.

### who

| Field | Content |
|---|---|
| kind | `container`, `unraid`, `gebruiker` (user), `onbekend` (unknown) |
| name | Container name, `SMB-share`, `shell (ssh)`, `mover`, `webGUI`, process name, … (commas replaced by spaces). Ends in ` (al gestopt)` ("already stopped") when the process was gone. |
| pid | PID (host namespace) |
| action | `geopend` (opened), `aangemaakt` (created), `verwijderd` (deleted), `verplaatst` (moved) |
| path | **Folders end in `/`.** May contain commas; always the last field. |

These values are Dutch and part of the format; the frontend translates them when shown.

## Web

| URL | Content |
|---|---|
| `/data/days.json` | `{"days": ["2026-10-08", "2026-10-09"]}`, ascending |
| `/data/days/YYYY-MM-DD.csv` | One day file. Any other path under `/data/` returns 404. |

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
