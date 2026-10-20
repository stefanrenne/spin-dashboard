# Todo

Open work, roughly by priority. Move finished items to `completed.md`.

## Before the first public release
- [ ] Test on a system with a classic array and parity (`/mnt/diskN`, `/dev/mdXpY`)
- [ ] Test with a ZFS pool with a dataset per share
- [ ] Verify the `shares.ini` field names on Unraid 6.12 (confirmed on 7.3.3: `useCache`, `cachePool`, `cachePool2`), and how a share with secondary storage (`cachePool2` set) should count; Settings › Paths per container shows "unknown" when they do not match
- [ ] Fill in the support link (`<Support>` in the template) once the forum thread exists
- [ ] Support thread on the Unraid forum and submission to Community Applications

## Features
- [ ] Test SAS drives and HBAs; validate the `smartctl -n standby` fallback
- [ ] Bytes per container: only possible for direct `/mnt/diskN` or pool mappings, see `docs/decisions.md`
- [ ] I/O in quiet hours: optionally also notify (webhook) when a pool reads or writes more than N MB inside the quiet hours, not only on a spin-up; useful for SSD pools that never spin down
- [ ] Reads and writes in the CSV export: the `io` lines of the period (per drive, KiB read and written) are not exported yet
