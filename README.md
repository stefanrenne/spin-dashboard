# Spin Dashboard for Unraid

See when your hard drives spin up and down, and **why**.

- A timeline per disk: every spin-up, how long it ran, how much of the day the disk was active.
- The cause of each spin-up: the first file that was opened, and **who** opened it: a Docker container (Plex, Sonarr, Bazarr, …), Unraid itself (mover, cron / User Scripts, webGUI, shfs) or a user (SMB/NFS share, shell).
- Click a spin-up to see every file that was opened during that session, with time and source.
- Rankings of what wakes your drives, by folder and by source.
- Browse back and forward through history: a day at a time in the 24-hour view, a week in the 7-day view, a month (30 days) in the 30-day view.
- Available in English, Dutch, French, German and Spanish. Follows your browser language; pick another one at the top right.
- Works with the array and with HDD pools (XFS, Btrfs, ZFS). SSD/NVMe pools are ignored.

## Install

**Community Applications:** search for *Spin Dashboard* and install with the defaults.

**Manual:** in Unraid, go to *Docker → Add Container → Template* and paste the template URL:

```
https://raw.githubusercontent.com/stefanrenne/spin-dashboard/main/unraid/spin-dashboard.xml
```

**docker run:**

```bash
docker run -d --name spin-dashboard \
  --privileged --pid=host \
  -p 8089:8089 \
  -v /mnt/user/appdata/spin-dashboard:/data \
  -v /mnt:/mnt:ro,slave \
  -v /var/local/emhttp:/emhttp:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  --restart unless-stopped \
  ghcr.io/stefanrenne/spin-dashboard:latest
```

**Docker Compose:**

```yaml
services:
  spin-dashboard:
    image: ghcr.io/stefanrenne/spin-dashboard:latest
    container_name: spin-dashboard
    privileged: true
    pid: host
    ports:
      - "8089:8089"
    volumes:
      - /mnt/user/appdata/spin-dashboard:/data
      - /mnt:/mnt:ro,slave
      - /var/local/emhttp:/emhttp:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      POLL_INTERVAL: "60"     # seconds between drive state checks
      RETENTION_DAYS: "30"    # days of history to keep
      # WATCH: "/mnt/disk1 /mnt/tank"  # optional, overrides automatic mount detection
      # IGNORE_PROCS: "cache_dirs"     # processes (and their children) that are not logged
    restart: unless-stopped
```

Keep the `/data` path on an SSD/cache pool (see [Tips](#tips)). The `/mnt`, `/emhttp` and Docker
socket mounts and the privileged/host-PID settings are explained under
[Permissions, and why](#permissions-and-why).

Open the WebUI on port 8089. The first drive check runs within a minute; file sources appear as soon as a disk is accessed.

## How it works

| Part | How |
|---|---|
| Drive state | `hdparm -C` every 60 s (falls back to `smartctl -n standby`). Neither wakes a sleeping drive. Only changes are stored. |
| Disk names | Read from Unraid's `/var/local/emhttp/disks.ini` (parity, disk1, pool members). |
| File access | `fatrace` (fanotify) on every HDD-backed mount, including ZFS datasets. |
| Source | The process behind each access is looked up in the host process table. Containers are recognised by cgroup and named via the Docker socket. Access through `/mnt/user` passes through shfs; the dashboard then looks up which process has the file open via `/mnt/user`. |
| Storage | Plain CSV files in the appdata folder: `spin.csv`, `disks.csv`, `who.csv`. History is kept for 30 days by default. |

## Permissions, and why

| Setting | Why |
|---|---|
| Privileged | fanotify on whole filesystems needs `CAP_SYS_ADMIN`; `hdparm` needs raw access to `/dev/sdX`. |
| `--pid=host` | To see which process (and which container) opened a file. |
| `/mnt` read-only, slave | To trace file access on the array and pools. Nothing is ever written there. |
| `/var/local/emhttp` read-only | Disk names and pool membership. |
| Docker socket read-only | Only to translate container IDs into names. |

## Tips

- **Keep appdata on an SSD/cache pool.** Otherwise the dashboard's own writes keep a drive awake.
- **Exclusive shares** (*Settings → Global Share Settings → Permit exclusive shares*) let the dashboard see the real source of more accesses, because they bypass shfs.
- **Cache Dirs** (*Dynamix Cache Directories*) keeps folder listings in memory, so apps like Sonarr and Radarr can check their root folders without waking a drive. Its scans are not logged (see *Ignore processes* / `IGNORE_PROCS`). Set *Scan user shares* to *No*, otherwise the scans show up as `shfs (via /mnt/user)`.
- **Auto-updaters** such as What's Up Docker or Watchtower recreate containers. If yours uses a high update threshold, check that it handles privileged containers correctly.

## Troubleshooting

| Symptom | Check |
|---|---|
| No disks in the dashboard | Container log: is `/var/local/emhttp` mounted and is the container privileged? |
| Spin-ups but no sources | Log should show `Bewaakt: /mnt/…`. If not, set *Watch override* to your mounts, e.g. `/mnt/disk1 /mnt/tank`. |
| Everything shows as `shfs (via /mnt/user)` | The access was too short to trace through shfs. Exclusive shares help. |
| Containers show as their process name | The Docker socket is not mounted. |

## Development

```
python -m pytest -q      # backend tests
npm install && npm test  # frontend tests
docker build -t spin-dashboard .
```

CI (`.github/workflows/ci.yml`) runs both test suites and then builds and pushes
`ghcr.io/<owner>/spin-dashboard` on every push to `main` and on tags `v*`.
More in [`docs/`](docs/) and [`CLAUDE.md`](CLAUDE.md).

## License

MIT
