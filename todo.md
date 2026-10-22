# Todo

Open work, roughly by priority. Move finished items to `completed.md`.

## Before the first public release
- [ ] Test on a system with a classic array and parity (`/mnt/diskN`, `/dev/mdXpY`)
- [ ] Test with a ZFS pool with a dataset per share
- [ ] Fill in the support link (`<Support>` in the template) once the forum thread exists
- [ ] Support thread on the Unraid forum and submission to Community Applications

## Features
- [ ] Test SAS drives and HBAs (needs the hardware). Plan:
  - With a SAS drive (or SATA behind an HBA) in standby: run `hdparm -C /dev/sdX`; if it says `unknown` or fails, `drive_state()` falls back to `smartctl -n standby -i`. Check that the fallback returns `standby` (output contains `STANDBY`, exit code 2) and `active` (exit code 0) correctly, and that neither call spins the drive up (watch the spin-up count in SMART, attribute 4, before and after)
  - The exit codes are now read as bit flags (only bits 0 and 1 mean no answer, see `docs/decisions.md`); confirm with real output that an active SAS drive does not set bit 1
  - SAS drives report standby through SCSI power conditions: confirm `smartctl -n standby` sees them, and try `sdparm --command=sense` as an alternative if not
  - Check `/sys/block/sdX/stat` counts I/O behind the HBA (the `io` lines) and that `rotational` is right for SAS SSDs
  - Record the findings in `docs/decisions.md` and add tests with the real `hdparm`/`smartctl` output
