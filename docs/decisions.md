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
A library scan through `/mnt/user` produces thousands of these events per second, and each lookup
used to read every `/proc/*/fd` link again. `fd_snapshot()` now reads them once into a map by file
name and reuses it for up to a second, but only for events that happened before the snapshot was
taken, so a file opened a moment ago is never missed.

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

## Pools and the array as one lane
Grouping happens only in the frontend, so the day files and the data contract stay per drive.
Membership comes from the names in `disks.ini`, the same rule as `mount_base()`: `trunk2` belongs to
pool `trunk`, `parity` and `diskN` form the array. This relies on Unraid not allowing pool names that
end in a digit. A group spins when at least one member spins, so members that wake together count as
one spin-up. One switch covers pools and the array together and is on by default. For the array
this hides which drive woke up (array drives spin up on their own); turning the switch off shows
every drive again.

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
4. With large folder trees (a photo library with thousands of thumbnail folders) `find` finishes so
   fast that fatrace itself can no longer read the process name and reports `unknown`, and the
   ancestors are gone too, so layers 1–3 never see a `cache_dirs` process. On one real day this was
   258,000 of 262,000 lines (46 MB instead of 0.5 MB), which made the dashboard slow to load. Since
   `find` only opens folders, `handle()` drops a **folder** open by a process that has already
   stopped and has no source, as long as an `IGNORE_PROCS` process is running (`ignore_active()`,
   checked at most every 30 s). File opens by unknown processes stay visible.

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

## Reads vs. writes
To explain why a drive never goes idle we distinguish reads from writes. `fatrace` is started with
`O+WD<` so that, besides opens, it also reports `W`: a write to a file that was already open. Without
that, a container that opens a log file or database once and then writes to it for hours would only
show a single `geopend` line, while those writes are exactly what keeps a drive spinning. An open
with write intent (fatrace shows `OW`/`ORW`) counts as a write too. In the day file both become the
action `geschreven`; a read-only open stays `geopend`. Older lines only knew `geopend`, so they
carry no direction and the frontend shows no read/write badge for them — the format is extended, not
migrated.

A `W` event fires on every write burst, so it is deduplicated per path + source + action per
`DEDUP_SECONDS`, exactly like opens; otherwise a busy share would flood the day file. Adding the
action to the dedup key means a read and a write of the same file each survive once per window.

The frontend's *Reads per drive* and *Writes per drive* cards answer "why is this drive never idle" directly from the same
activity stream: per drive the top write and read sources and files, even when there were no
spin-ups at all in the period.

## Lookups must not scan
The frontend links every file access to its source (`who` line) by path and time. The first
version scanned the full list for that path on every lookup. Paths such as a share root have
thousands of lines, and the Reads/Writes per drive cards look up every event in the period, so the work
grew quadratically: 110,000 real lines took about 2 minutes to process. The who index is now sorted
by time per path, with a separate index for SMB/NFS lines, and `nearestWho` uses binary search.
Profile with a realistic data set (`node --cpu-prof` on the page in jsdom works well) before
assuming the network is the bottleneck.

## No bytes per container (yet)
It is tempting to show how much data each container read and wrote, not only how often. Two things
stand in the way. The container runs in its own cgroup namespace, so it cannot read other containers'
`io.stat`; Docker's stats API (`/containers/<id>/stats?one-shot=true`) works, but costs a call per
container per poll. More importantly, access through `/mnt/user` goes through `shfs` (FUSE): the disk
I/O is charged to `shfs`, not to the container. With the usual Unraid setup (everything via
`/mnt/user`) the numbers would be close to zero for exactly the containers that matter. Only
containers that map `/mnt/diskN` or a pool directly would get real numbers. Until there is a way to
attribute `shfs` I/O, counts of accesses (after dedup) are the honest measure.

## Quiet hours notify through a webhook, not Unraid's notifications
Unraid's own notifications are sent by a script on the host (`/usr/local/emhttp/webGui/scripts/notify`).
The container cannot run it without stepping outside what it is allowed to do, and writing into
`/var/local/emhttp` would break the read-only mount. A webhook works with what people already use
(ntfy, Gotify, Discord, Slack, Home Assistant) and needs only `urllib`. The JSON carries the same
sentence as `text` and `content`, so most receivers need no template.

Like every setting, the webhook can be changed by anyone who can reach the dashboard; the server then
POSTs fixed JSON to that URL on a spin-up in quiet hours or on "Send a test". That is the same trust
model as the rest of the dashboard (meant for the local network, no login); put it behind the
reverse proxy's authentication when it is reachable from outside. The test endpoint only uses the
saved URL, never one from the request.

## SSD wear from SMART and from what the dashboard measures

What an SSD writes per day comes from the `io` lines the dashboard already writes, so it works for every SSD
and covers exactly the period on screen. The real wear (how much of the rated endurance is used, everything
written since new) only the drive knows, so the poller asks SMART too: `smartctl` on an SSD wakes nothing,
and the poller only runs it for drives the kernel calls non-rotational, with `-n standby` as a second guard
should that ever be wrong. Once an hour is enough: wear moves by a percent in months. The warning is a
limit per SSD rather than per pool, because a mirrored pool writes everything twice and wear is per drive.
It is not sent to the webhook: a slow trend is not something to be woken for.

## Looking up a file through /mnt/user only when no sleeping drive can be touched

To name the container behind an access through `/mnt/user`, the watcher compares inodes: it stats the file
through `/mnt/user` and looks for a process that has the same file open. That stat goes through shfs, which
may look the path up on every disk or pool the share can use, so on an array with sleeping disks it could
touch one of them (hard rule 1). Whether it really does was never measured; the lookup is therefore guarded
instead of removed: from the share settings (`shares.ini`: storage, primary and secondary pool,
`include`/`exclude`) the watcher knows where the share can live, and it only looks up when every one of those
places, apart from the drive of the event itself (which is spinning), is an SSD or a spinning HDD according to
the last poll. Unknown settings or drives: no lookup. The access is then logged as `shfs (via /mnt/user)`,
less precise but never a reason for a drive to wake up.

## Waiting lines are written when the drive of DATA_DIR is awake

The `io` and `wear` lines wait in memory and are written when that cannot wake a drive: when the drive that
holds `DATA_DIR` spins or is an SSD. The poller finds that drive in `/proc/self/mountinfo` (see
`data-formats.md`); when it cannot tell (a user share that can live on the array, an unknown device), it falls
back to "any HDD spins". The `who` lines are not held back the same way: an access on one HDD is written at
once, even if `DATA_DIR` sits on another, sleeping HDD. Holding them could take days on a quiet system, would
grow without bound in memory and would lose them on a crash; keep appdata on an SSD (README, Tips).

## A settings token instead of a login

The dashboard is meant for the LAN, and a login would mean users, passwords and sessions in a container that
should stay small. What can do harm through the dashboard is changing its settings, above all the webhook URL
the server then POSTs to; reading is covered by keeping it on the LAN or behind a reverse proxy with a login
(README, Security). So one optional token (`SETTINGS_TOKEN`) guards every change, and the webhook URL itself is
never sent to a browser in full: only scheme and host, and that masked form sent back means "unchanged", so a
browser that saves the quiet hours does not have to know the real URL.
