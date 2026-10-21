# Todo

Open work, roughly by priority. Move finished items to `completed.md`.

## Before the first public release
- [ ] Test on a system with a classic array and parity (`/mnt/diskN`, `/dev/mdXpY`)
- [ ] Test with a ZFS pool with a dataset per share
- [ ] Verify the `shares.ini` field names on Unraid 6.12 (confirmed on 7.3.3: `useCache`, `cachePool`, `cachePool2`), and how a share with secondary storage (`cachePool2` set) should count; Settings › Paths per container shows "unknown" when they do not match
- [ ] Fill in the support link (`<Support>` in the template) once the forum thread exists
- [ ] Support thread on the Unraid forum and submission to Community Applications
- [ ] Version and changelog: no `v*` tag exists yet. A changelog section per version (from `completed.md`) and the version in the page footer and `/health`

## Performance
- [ ] Large downloads with "All": day files are ~6 MB a day on a busy server (mostly `who` lines), so 30 days is ~180 MB. Measure again now that SSD pools log only writes and live days are fetched in parts; only if it is still too much: a per-day summary endpoint (like `spins.csv`) for the cards that do not need single lines, or filtering `who` lines on the server by the window

## Watcher and data quality
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
