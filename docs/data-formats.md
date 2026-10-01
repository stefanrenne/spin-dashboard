# Dataformaten

Alle bestanden staan in `DATA_DIR` (standaard `/data`, op de host meestal
`/mnt/user/appdata/spin-dashboard`). Platte tekst, één regel per record, geen header.

## spin.csv

```
2026-10-01T04:15:07+02:00,/dev/sdd,active
2026-10-01T05:27:12+02:00,/dev/sdd,standby
```

| Veld | Inhoud |
|---|---|
| 1 | ISO 8601 met offset (tijdzone van de container, `TZ` wordt door Unraid meegegeven) |
| 2 | Device, `/dev/sdX` |
| 3 | `active` of `standby` |

Alleen wijzigingen. De frontend accepteert ook syslog-regels (`spinning down /dev/sdX`,
`read SMART /dev/sdX`) voor handmatige import in oudere versies.

## disks.csv

```
sdb,parity
sdc,disk1
sdd,trunk
sde,trunk2
```

Device en naam uit `disks.ini`. Wordt alleen herschreven als de inhoud verandert.

## who.csv

```
1790820907,container,bazarr,3562763,geopend,/mnt/trunk/Media/series/x.mkv
1790820910,gebruiker,SMB-share,1878514,geopend,/mnt/trunk/Media/
1790820912,unraid,mover,22011,aangemaakt,/mnt/trunk/Media/films/
```

| Veld | Inhoud |
|---|---|
| 1 | Epoch-seconden |
| 2 | Soort: `container`, `unraid`, `gebruiker`, `onbekend` |
| 3 | Naam: containernaam, `SMB-share`, `shell (ssh)`, `mover`, `webGUI`, procesnaam, … (komma's vervangen door spaties) |
| 4 | PID (host-namespace) |
| 5 | Actie: `geopend`, `aangemaakt`, `verwijderd`, `verplaatst` |
| 6 | Pad. **Mappen eindigen op `/`.** Mag komma's bevatten; altijd het laatste veld. |

Oudere regels kunnen mappen zonder slash bevatten; de frontend herkent die heuristisch
(pool/share-root, of laatste deel zonder punt).

## activity.csv (verouderd, optioneel)

```
1790665701,trunk,OPEN+ISDIR,/mnt/trunk/
```

Uitvoer van de oude inotify-bewaker. Wordt niet meer geschreven maar nog wel gelezen.

## Bewaartermijn

`spin.csv` en `who.csv` worden elke 6 uur ingekort tot `RETENTION_DAYS` (standaard 30).
Regels zonder herkenbare tijd blijven staan.
