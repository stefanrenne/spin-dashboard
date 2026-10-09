# Dataformaten

Alle data staat in `DATA_DIR/days` (standaard `/data/days`, op de host meestal
`/mnt/user/appdata/spin-dashboard/days`): **één CSV per dag**, `YYYY-MM-DD.csv`. De dag is de
lokale datum van de container (`TZ`, door Unraid meegegeven). Platte tekst, geen header.

## Regels

Elke regel begint met epoch-seconden en een soort; daarna velden per soort.

```
1791496800,disk,sdd,trunk
1791496800,state,sdd,standby
1791529042,spin,sdd,active
1791529032,who,container,sonarr,16690,geopend,/mnt/trunk/Media/series/
1791530846,spin,sdd,standby
```

| Soort | Velden na `epoch,soort` | Betekenis |
|---|---|---|
| `disk` | device, naam | Naam uit `disks.ini` (`parity`, `disk1`, `trunk`, `trunk2`). Bij de eerste peiling van elke dag en als de namen veranderen. |
| `state` | device, `active`/`standby` | Gemeten status op dat moment, **geen wijziging**: bij de eerste peiling van een dag en na het starten. Telt niet als spin-up. |
| `spin` | device, `active`/`standby` | Statuswijziging tussen twee peilingen. `active` na `standby` is een spin-up. |
| `who` | soort, naam, pid, actie, pad | Toegang tot een bestand of map (fatrace). Zie hieronder. |
| `activity` | schijf, event, pad | Oude inotify-data (`OPEN+ISDIR`, `MODIFY`, …). Wordt alleen nog door de migratie geschreven. |

Door de `disk`- en `state`-regels bovenaan is elk dagbestand op zichzelf leesbaar: namen en de
status bij het begin van de dag staan erin, ook als de vorige dag al is opgeruimd.

### who

| Veld | Inhoud |
|---|---|
| soort | `container`, `unraid`, `gebruiker`, `onbekend` |
| naam | Containernaam, `SMB-share`, `shell (ssh)`, `mover`, `webGUI`, procesnaam, … (komma's vervangen door spaties). `… (al gestopt)` als het proces weg was. |
| pid | PID (host-namespace) |
| actie | `geopend`, `aangemaakt`, `verwijderd`, `verplaatst` |
| pad | **Mappen eindigen op `/`.** Mag komma's bevatten; altijd het laatste veld. |

Deze waarden zijn Nederlands en maken deel uit van het formaat; de frontend vertaalt ze bij het tonen.

## Web

| URL | Inhoud |
|---|---|
| `/data/days.json` | `{"days": ["2026-10-08", "2026-10-09"]}`, oplopend |
| `/data/days/YYYY-MM-DD.csv` | Eén dagbestand. Andere paden onder `/data/` geven 404. |

## Bewaartermijn

Elke 6 uur worden dagbestanden ouder dan `RETENTION_DAYS` (standaard 30) in zijn geheel verwijderd.

## Migratie van het oude formaat

Tot oktober 2026 stond alles in losse bestanden in `DATA_DIR`:

| Bestand | Regel | Wordt |
|---|---|---|
| `spin.csv` | `2026-10-01T04:15:07+02:00,/dev/sdd,active` | `spin` |
| `disks.csv` | `sdd,trunk` | `disk` bovenaan elke gemigreerde dag |
| `who.csv` | `epoch,soort,naam,pid,actie,pad` | `who` |
| `activity.csv` | `epoch,disk,EVENT,pad` | `activity` |

`migrate()` draait bij elke start. Staan er oude bestanden, dan worden ze naar dagbestanden
omgezet (met per dag `disk`- en `state`-regels, berekend uit de spin-historie), samengevoegd met
wat er al in de dagbestanden stond, en daarna verplaatst naar `DATA_DIR/legacy/`. Herhalen is
veilig: dubbele regels worden samengevoegd. Regels die niet om te zetten zijn (zoals
syslog-regels in `spin.csv`) worden overgeslagen en geteld in de log; ze blijven in `legacy/` staan.
