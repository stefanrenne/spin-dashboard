# Changelog

What changed per version, for people who run Spin Dashboard. The detailed log of the work is in
`completed.md`; the data formats and their migrations are in `docs/data-formats.md`.

## [Unreleased]

### Added
- Live activity can be filtered per pool (and the array) with checkboxes; the choice is kept per browser.
- Unassigned Devices: drives mounted by the plugin at `/mnt/disks/<label>` are followed like pools, each as its own lane.
- Bytes per container: what each container read and wrote per drive, in its profile and on the drive pages.

## [0.9.0] - 2026-10-21

The first tagged version: everything up to now. Feature complete for a first release; the tests on a
classic array with parity and on a ZFS pool with a dataset per share, and the submission to Community
Applications, are still to come before 1.0.0.

### Spin-ups and their cause
- A timeline per pool and the array with every spin-up, how long it ran and how much of the time a drive
  was active; pools and the array as one lane, or every drive on its own.
- The cause of each spin-up: the first file that was opened and who opened it: a container (with the
  process inside it, "plex › Plex Media Scan"), Unraid itself (mover, cron, webGUI, shfs) or a user
  (SMB/NFS, shell). Access through `/mnt/user` is traced back to the container that has the file open.
- Key figures with the comparison to the previous period, spin-ups per day and per hour of day, causes per
  folder, activity, rhythm and "kept awake" per source, a timeline per source, spin time and rest per drive.
- Container start, stop, restart, health and out-of-memory kills, named when they come right before a
  spin-up.
- Overview of every pool, drive and container, with detail pages per container, pool and drive.

### Reads, writes and SSDs
- Every access labelled read or write; Reads per drive and Writes per drive.
- Reads and writes of every drive, SSDs and NVMe included, from the kernel's counters (never wakes a
  drive); SSD lanes with a read/write graph.
- File access on SSD pools, per pool, only writes unless "also reads" is chosen.
- SSD wear: what the SSDs write per day, SMART wear and total written, a limit that marks an SSD that
  writes too much, and an estimate of when the rated endurance is reached.

### Notifications and export
- Quiet hours: spin-ups inside them are marked and can be sent to a webhook (ntfy, Gotify, Discord, Slack,
  Home Assistant); optionally also reads and writes above a limit, and SSD wear.
- CSV export of the period: spin-ups, file access, container events, reads and writes per hour, wear.

### Using it
- English, Dutch, French, German and Spanish.
- Cards can be hidden, moved and made small or large; hidden paths with wildcards, stored on the server.
- Live activity feed over Server-Sent Events; only the new part of today is fetched every minute.
- `/health` for Docker's healthcheck and auto-heal containers.
- Optional `SETTINGS_TOKEN` so only who has it can change the settings; the webhook URL is never sent to
  the browser in full.

### Safety
- Never wakes a sleeping drive: spin state only with `hdparm -C` / `smartctl -n standby`, SMART only on
  SSDs, lookups through `/mnt/user` only when no sleeping drive can be touched.
- Never logs its own reads and writes, and only writes its data when that cannot wake the drive that holds it.
