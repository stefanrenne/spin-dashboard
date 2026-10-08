# Architectuur

```
┌──────────────────────── container (privileged, --pid=host) ────────────────────────┐
│                                                                                    │
│  poller ── hdparm -C / smartctl -n standby ──► spin.csv   (alleen wijzigingen)     │
│     └── /emhttp/disks.ini + /sys/block/*/rotational ──► disks.csv                  │
│                                                                                    │
│  watcher ── fatrace -c per HDD-mount ──► wachtrij ──► handle() ──► who.csv          │
│                                         classify(pid): /proc/<pid>/cgroup, stat     │
│                                         container_name(): docker.sock              │
│                                         shfs → via_user_share(): /proc/*/fd       │
│                                                                                    │
│  housekeeping ── trim() elke 6 uur, relabel_existing() bij start                   │
│                                                                                    │
│  web ── ThreadingHTTPServer :8089 ── /  → static/index.html                        │
│                                      /data/*.csv → DATA_DIR                        │
└────────────────────────────────────────────────────────────────────────────────────┘
          ▲ /mnt (ro,slave)   ▲ /var/local/emhttp (ro)   ▲ docker.sock (ro)   ▼ /data (rw)
```

## Onderdelen

**Poller.** Elke `POLL_INTERVAL` seconden. Bepaalt de HDD's uit `disks.ini` (alleen
`rotational=1`), peilt de status en schrijft alleen statuswijzigingen weg. Bij de eerste peiling
na het starten wordt de huidige status van elke schijf vastgelegd.

**Watcher.** Bepaalt de te bewaken mounts: `/mnt/<naam>` voor array-schijven (`disk1`) en
pools (poolleden `trunk2` horen bij `/mnt/trunk`), plus alle mounts daaronder (ZFS-datasets).
Per mount draait `fatrace -c` met filter `O+D<` (open, create, delete, move). `fatrace` gebruikt
`FAN_MARK_FILESYSTEM`, dus ook toegang vanuit andere mount-namespaces (host, andere containers)
komt binnen. Elke minuut wordt gecontroleerd of de mounts veranderd zijn of een `fatrace` gestopt is.

**Classificatie.** Processen uit `IGNORE_PROCS` (standaard `cache_dirs`), of met zo'n proces als
voorouder, worden niet gelogd. Daarna de volgorde: container (cgroup) → gestopt proces → SMB/NFS → shell (sshd, ttyd,
login) → shfs → btrfs → mover → cron → webGUI → procesnaam. Opens door `shfs` worden 0,4 s later
opnieuw bekeken: welk ander proces heeft hetzelfde bestand via `/mnt/user` open (inode-vergelijking)?

**Deduplicatie.** Zelfde pad + soort + naam maar één keer per `DEDUP_SECONDS` (600). SMB/NFS wordt
samengevat tot één regel per share.

**Frontend.** Haalt elke minuut de CSV's op (conditioneel, 304 bij geen wijziging), voegt
`activity.csv` (oude inotify-data, optioneel) en `who.csv` samen tot één activiteitenstroom en
koppelt die aan spin-ups. Zie `docs/data-formats.md`.

## Waarom één proces

Eerder bestond dit uit een User Script (`spinmon.sh`), een inotify-bewaker, nginx en een aparte
`spin-who`-container. Eén proces met threads maakt installatie via Community Applications mogelijk
en voorkomt dat onderdelen los van elkaar stoppen.
