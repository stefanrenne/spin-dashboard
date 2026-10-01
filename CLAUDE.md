# CLAUDE.md

Context voor Claude (en andere ontwikkelaars) die aan dit project werken.

## Wat dit is

Spin Dashboard: een Unraid-app (één Docker-container) die laat zien wanneer de HDD's op een
Unraid-server opspinnen en **waarom**: welk bestand werd geopend en door welk proces
(container, Unraid zelf of een gebruiker via SMB/shell). Distributie via Community Applications.

## Structuur

| Pad | Wat |
|---|---|
| `app/spindash.py` | Het hele backend-proces: poller, watcher (fatrace), webserver, onderhoud. Alleen stdlib. |
| `app/static/index.html` | Het dashboard. Eén bestand, vanilla JS, geen build-stap. |
| `tests/` | `pytest` voor de backend, `tests/js/` voor de frontend (node:test + jsdom). |
| `unraid/` | Community Applications-template en icoon. |
| `docs/` | Architectuur, dataformaten, ontwikkeling, publiceren, ontwerpkeuzes. |
| `todo.md` / `completed.md` | Open werk en afgerond werk. Werk deze bij als je iets oppakt of afrondt. |

## Commando's

```bash
python -m pytest -q          # backend-tests
npm install && npm test      # frontend-tests
docker build -t spin-dashboard .
# lokaal draaien zonder Unraid: zie docs/development.md
```

Voer beide testsets uit voor elke commit. CI (`.github/workflows/ci.yml`) doet hetzelfde en bouwt
daarna pas het image.

## Harde regels

1. **Nooit een slapende schijf wakker maken.** Spin-status alleen via `hdparm -C` of
   `smartctl -n standby`. Geen `stat`, `ls` of `smartctl -a` op HDD's vanuit de poller.
   Een `os.path.isdir()` in de watcher mag alleen ná een event, als de schijf al draait.
2. **Nooit schrijven onder `/mnt`.** `/mnt` is read-only gemount; alle uitvoer gaat naar `DATA_DIR`.
3. **Eigen schrijfacties niet loggen** (eigen PID en eigen bestandsnamen worden overgeslagen),
   anders houdt het dashboard zichzelf wakker als appdata op een HDD staat.
4. **Alleen stdlib in de backend.** Geen pip-dependencies in het image.
5. **Dataformaten zijn een contract** tussen backend en frontend en met bestaande installaties.
   Wijzig ze alleen achterwaarts compatibel; zie `docs/data-formats.md`.
6. **Host-paden via module-constanten** (`PROC`, `SYS_BLOCK`, `MOUNTS_FILE`, …) zodat tests ze kunnen vervangen.

## Conventies

- Commentaar, logregels en de UI zijn **Nederlands**. README en het CA-template zijn **Engels**
  (internationaal publiek). Tweetalige UI staat in `todo.md`.
- Tijden: spin.csv in ISO 8601 met offset, who.csv in epoch-seconden. De frontend toont lokale tijd.
- Kleine, gerichte wijzigingen. Nieuwe logica krijgt een test.
- Frontend: geen frameworks, geen externe assets behalve Google Fonts met fallback.

## Valkuilen die we al tegenkwamen

Zie `docs/decisions.md` voor de achtergrond. Kort:
- Containers zien andere containers' cgroups als `0::/../<id>` (cgroup-namespace). Match op het 64-hex ID.
- Toegang via `/mnt/user` loopt door `shfs`; de echte opener wordt via `/proc/*/fd` en inodes gezocht.
- `fatrace` geeft mappen zonder slash; de watcher voegt die toe.
- Auto-updaters (What's Up Docker) kunnen deze privileged container verwijderen en niet herstellen.
