# Design decisions and lessons

Background for choices that are not self-evident. Newest at the bottom.

## hdparm -C instead of syslog
Unraid logs `spinning down` but no reliable spin-up. `hdparm -C` queries the state without
waking the drive. Polling once a minute is plenty; spin-ups are linked within that minute to the
access that caused them (window: 3 minutes before, 30 seconds after).

## fanotify (fatrace) instead of inotify
inotify does see which file, but not which process. fanotify reports the PID. `fatrace` ≥ 0.17
uses `FAN_MARK_FILESYSTEM`, so access from other mount namespaces is visible too, and it supports
ZFS explicitly (one instance per dataset, because every dataset is its own filesystem).
After that, the inotify watcher became redundant.

## shfs and /mnt/user
Access via `/mnt/user` arrives on the pool as `shfs`. The FUSE mount itself cannot be followed
with fanotify (fatrace skips `shfs`). Solution: shortly after the event, look up which process has
the file open via `/mnt/user`, based on the inode. This works for files that stay open for a
moment; short accesses and folder listings remain `shfs`. Exclusive shares bypass shfs.

## cgroup namespace
Docker gives containers their own cgroup namespace by default. Other containers then show up as
`0::/../<id>` instead of `0::/docker/<id>`. `cgroup: host` in compose helped but broke the
Portainer deploy; matching on the 64-hex ID works in every case.

## SMB per share
An SMB client browsing a folder opens hundreds of files. One line per share per 10 minutes is
enough to know *that* someone was busy via SMB.

## Counting pool wake-ups once
Pool members spin up together. In the table they are grouped (within 2 minutes, same kind), and
the rankings count such a group as a single event.

## Auto-updaters
What's Up Docker with `THRESHOLD=all` saw `python:3.15-rc-windowsservercore-ltsc2025` as an update
of `python:3.13-slim`, removed the container and could not start the new one. The image therefore
pins `python:3.13-slim-trixie`, and the README warns about it.

## Ignoring Cache Dirs

The Dynamix Cache Directories plugin keeps folder data in memory by continuously running `find`
over the shares. Those are opens that fatrace sees but that do not wake a drive; if logged, they
would fill the day files (one line per folder per `DEDUP_SECONDS`) and dominate the rankings.
That is why `classify()` skips processes in `IGNORE_PROCS` and their descendants, by process name
or by command line (`bash /path/cache_dirs`).

In practice (cache_dirs 2.2.9) the chain is `cache_dirs` → subshell → `timeout` → `find`, and `find`
walks thousands of folders in a fraction of a second. The consumer then lags seconds behind: the
process is already gone and the events showed up en masse as `find (al gestopt)`, sometimes also
as `unraid,find` when the chain was already half broken. Hence three layers:

1. `classify_early()` determines the source in the reader thread, as soon as a new PID arrives.
2. `classify()` remembers the result per PID (and process name, against PID reuse) for `PID_TTL` (30 s).
3. Safety net: if a process with the same name was ignored within the last `IGNORE_TTL` (300 s),
   a process with that name without a recognisable source (`(al gestopt)` or just the process name)
   is ignored too. A `find` from a shell, SMB or cron does have a source and stays visible; a
   stand-alone `find` without a source within those five minutes is unfortunately dropped as well.

## Time zones
Unraid can run on UTC while containers use local time; cron on the host then counts differently
from the apps. All lines therefore use epoch seconds; only the file name (the day) depends on the
container's `TZ`.

## One file per day
There used to be four separate files (`spin.csv`, `disks.csv`, `who.csv`, `activity.csv`), each with
its own time format, that were rewritten completely on every cleanup run. Now everything for a day
lives in one file with a type per line: cleanup means deleting a file, a day can be viewed or
exported on its own, and the browser fetches older days conditionally (304). Every day starts with
`disk` and `state` lines so it can be read without the previous day; `state` is explicitly not a
spin-up, so a restart or the change of day does not produce false spin-ups.
