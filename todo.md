# Todo

Open work, roughly by priority. Move finished items to `completed.md`.

## Before the first public release
- [ ] Build and test the image on an array-less setup with a Btrfs pool
- [ ] Test on a system with a classic array and parity (`/mnt/diskN`, `/dev/mdXpY`)
- [ ] Test with a ZFS pool with a dataset per share
- [ ] Fill in the support link (`<Support>` in the template) once the forum thread exists
- [ ] Support thread on the Unraid forum and submission to Community Applications

## Features
- [ ] Detect writes to existing files (fatrace `W`), deduplicated, so parity spin-ups are easier to explain
- [ ] Test SAS drives and HBAs; validate the `smartctl -n standby` fallback
- [ ] Optional: notifications (Unraid notification or webhook) for spin-ups outside a configured window
- [ ] Optional: set an "expected night window" per pool and highlight deviations
- [ ] Export a period as CSV from the UI

## Technical
- [ ] Health endpoint (`/health`) with poller and watcher status, used by the Docker HEALTHCHECK
- [ ] Make `via_user_share` faster with many processes (cache fd scans per second)
- [ ] Large data sets: have the frontend fetch and process only the days of the selected range
- [ ] Multi-arch image not needed (Unraid is amd64), but document it
