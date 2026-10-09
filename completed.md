# Afgerond

Nieuwste bovenaan.

## 2026-10-09: talen en bladeren
- UI in het Engels, Nederlands, Frans, Duits en Spaans; standaard de browsertaal, keuze wordt onthouden
- Bladeren door de tijd: per dag (24 uur), per week (7 dagen) en per 30 dagen, met knop "Nu"
- Aslabels korter en uitgedund op smalle schermen
- Info-icoontjes met tooltip in "Wat maakt de schijven wakker" (Root van een pool, Onbekend, shfs, al gestopt) in plaats van uitlegalinea's
- Frontendtests met `--test-force-exit`, zodat een mislukte test niet blijft hangen op de verversingstimer

## 2026-10-08: Cache Dirs
- Processen uit `IGNORE_PROCS` (standaard `cache_dirs`) en hun kinderen worden niet gelogd
- Bron per PID al bij binnenkomst bepalen en 30 s onthouden: events van kortlevende processen niet meer als `(al gestopt)`
- Cache Dirs ook herkennen via de opdrachtregel en, als vangnet, aan dezelfde procesnaam zonder bron
- README: `docker run`- en Docker Compose-voorbeeld, tip over Cache Dirs

## 2026-10-01: Unraid-app
- Alles samengevoegd in één container (`app/spindash.py`): poller, fatrace-watcher, webserver, onderhoud
- Pools en schijfnamen uit `disks.ini` in plaats van `zpool`/`lsblk` op de host
- `smartctl -n standby` als terugval voor `hdparm`
- Eigen schrijfacties worden niet gelogd
- Opruimen op leeftijd (`RETENTION_DAYS`) in plaats van op bestandsgrootte
- Community Applications-template, icoon, Dockerfile, CI (tests → image naar GHCR)
- Unit-tests: backend (pytest) en frontend (node:test + jsdom)
- Ranglijsten tellen pool-wake-ups als één gebeurtenis
- Oudere SMB-regels per bestand worden onder de share gegroepeerd

## 2026-09-29 – 2026-09-30: oorzaak en bron
- `spin-who`: fanotify via `fatrace`, bron per toegang (container / Unraid / gebruiker)
- Containerherkenning ongeacht cgroup-namespace; containernamen via docker.sock
- Bron achter `shfs` via inode-vergelijking op `/proc/*/fd`
- Oudere regels alsnog aan containers koppelen bij het starten
- SMB/NFS samengevat per share
- Sessielijst: alle bestanden per draai-sessie, met tijd en bron
- Gebeurtenissen van poolleden gegroepeerd in één rij
- `who.csv` als volwaardige bron naast (en later in plaats van) `activity.csv`
- Labels zonder "Unraid:"-prefix; soort zichtbaar via kleur

## 2026-09-28 – 2026-09-29: dashboard en verzameling
- Dashboard met tijdlijn per schijf, histogram per uur, oorzakenranglijst en gebeurtenissentabel
- Live data via nginx-container, elke minuut ververst
- `spinmon.sh`: spin-status per minuut, alleen wijzigingen; inotify-bewaker voor de wekker en sessies
- Pooldetectie voor array, XFS/Btrfs en ZFS
- Robuuste bewaker: herstart, opruimen van wezen, nette stop via procesgroep
- Root-mappenlijsten herkend en uitgelegd ("Root van pool")
