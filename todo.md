# Todo

Open work, roughly by priority. Move finished items to `completed.md`.

## Before the first public release
- [ ] Test on a system with a classic array and parity (`/mnt/diskN`, `/dev/mdXpY`)
- [ ] Test with a ZFS pool with a dataset per share
- [ ] Verify the `shares.ini` field names on Unraid 6.12 (confirmed on 7.3.3: `useCache`, `cachePool`, `cachePool2`), and how a share with secondary storage (`cachePool2` set) should count; Settings › Paths per container shows "unknown" when they do not match
- [ ] Fill in the support link (`<Support>` in the template) once the forum thread exists
- [ ] Support thread on the Unraid forum and submission to Community Applications

## Features
- [ ] Test SAS drives and HBAs (needs the hardware). Plan:
  - With a SAS drive (or SATA behind an HBA) in standby: run `hdparm -C /dev/sdX`; if it says `unknown` or fails, `drive_state()` falls back to `smartctl -n standby -i`. Check that the fallback returns `standby` (output contains `STANDBY`, exit code 2) and `active` (exit code 0) correctly, and that neither call spins the drive up (watch the spin-up count in SMART, attribute 4, before and after)
  - Check the exit codes: `smartctl` sets bit flags (for example bit 2 when a SMART command failed) even when the drive answered; if a drive returns non-zero while active, accept "no STANDBY in the output" as `active` and add a test with that output
  - SAS drives report standby through SCSI power conditions: confirm `smartctl -n standby` sees them, and try `sdparm --command=sense` as an alternative if not
  - Check `/sys/block/sdX/stat` counts I/O behind the HBA (the `io` lines) and that `rotational` is right for SAS SSDs
  - Record the findings in `docs/decisions.md` and add tests with the real `hdparm`/`smartctl` output
