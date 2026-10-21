# Todo

Open work, roughly by priority. Move finished items to `completed.md`.

## Before the first public release
- [ ] Test on a system with a classic array and parity (`/mnt/diskN`, `/dev/mdXpY`)
- [ ] Test with a ZFS pool with a dataset per share
- [ ] Verify the `shares.ini` field names on Unraid 6.12 (confirmed on 7.3.3: `useCache`, `cachePool`, `cachePool2`), and how a share with secondary storage (`cachePool2` set) should count; Settings › Paths per container shows "unknown" when they do not match
- [ ] Fill in the support link (`<Support>` in the template) once the forum thread exists
- [ ] Support thread on the Unraid forum and submission to Community Applications

## Features
- [ ] Unassigned Devices: HDDs mounted by the Unassigned Devices plugin (`/mnt/disks/<name>`) are not in `disks.ini`, so they are neither polled nor watched, and Settings › Paths per container shows `/mnt/disks` and `/mnt/remotes` as a pool called "disks" or "remotes". Find where the plugin keeps device and mount point (without touching the drives) and treat them like pools; network mounts under `/mnt/remotes` stay out
- [ ] Test SAS drives and HBAs; validate the `smartctl -n standby` fallback
- [ ] Bytes per container: only possible for direct `/mnt/diskN` or pool mappings, see `docs/decisions.md`
