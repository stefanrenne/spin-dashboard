# Ontwerpkeuzes en lessen

Achtergrond bij keuzes die niet vanzelfsprekend zijn. Nieuwste onderaan.

## hdparm -C in plaats van syslog
Unraid logt `spinning down` maar geen betrouwbare spin-up. `hdparm -C` vraagt de status op
zonder de schijf te wekken. Peilen per minuut is ruim voldoende; spin-ups worden binnen die minuut
gekoppeld aan de toegang die ze veroorzaakte (venster: 3 minuten ervoor, 30 seconden erna).

## fanotify (fatrace) in plaats van inotify
inotify ziet wel welk bestand, maar niet welk proces. fanotify geeft de PID. `fatrace` ≥ 0.17
gebruikt `FAN_MARK_FILESYSTEM`, zodat ook toegang vanuit andere mount-namespaces zichtbaar is,
en ondersteunt ZFS expliciet (per dataset één instantie, want elke dataset is een eigen filesystem).
De inotify-bewaker is daarna overbodig geworden.

## shfs en /mnt/user
Toegang via `/mnt/user` komt op de pool binnen als `shfs`. De FUSE-mount zelf is niet met
fanotify te volgen (fatrace slaat `shfs` over). Oplossing: kort na het event zoeken welk proces
het bestand via `/mnt/user` open heeft, op basis van inode. Werkt voor bestanden die even open
blijven; korte toegangen en mappenlijsten blijven `shfs`. Exclusieve shares omzeilen shfs.

## cgroup-namespace
Docker geeft containers standaard een eigen cgroup-namespace. Andere containers zijn dan zichtbaar
als `0::/../<id>` in plaats van `0::/docker/<id>`. `cgroup: host` in compose hielp, maar brak de
Portainer-deploy; matchen op het 64-hex ID werkt in alle gevallen.

## SMB per share
Een SMB-client die een map doorbladert, opent honderden bestanden. Per share één regel per
10 minuten is genoeg om te weten dát iemand via SMB bezig was.

## Pool-wake-ups één keer tellen
Poolleden spinnen samen op. In de tabel worden ze gegroepeerd (binnen 2 minuten, zelfde soort), en
de ranglijsten tellen zo'n groep als één gebeurtenis.

## Auto-updaters
What's Up Docker met `THRESHOLD=all` zag `python:3.15-rc-windowsservercore-ltsc2025` als update van
`python:3.13-slim`, verwijderde de container en kon de nieuwe niet starten. Het image pint daarom
op `python:3.13-slim-trixie`, en de README waarschuwt.

## Cache Dirs negeren

De plugin Dynamix Cache Directories houdt mapgegevens in het geheugen door continu `find` over de
shares te draaien. Dat zijn opens die fatrace ziet, maar die geen schijf wekken; gelogd zouden ze
`who.csv` vullen (één regel per map per `DEDUP_SECONDS`) en de ranglijsten domineren. Daarom slaat
`classify()` processen uit `IGNORE_PROCS` en hun kinderen over. Beperking: is `find` al gestopt
voordat het event verwerkt wordt, dan is de ouder niet meer te zien en verschijnt het als
`find (al gestopt)`.

## Tijdzones
Unraid kan op UTC staan terwijl containers lokale tijd gebruiken; cron op de host rekent dan anders
dan de apps. spin.csv schrijft daarom altijd een expliciete offset, en who.csv epoch-seconden.
