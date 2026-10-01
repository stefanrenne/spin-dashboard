# Todo

Open werk, grofweg op prioriteit. Verplaats afgeronde punten naar `completed.md`.

## Voor de eerste publieke release
- [ ] Image bouwen en testen op een array-loze setup met een Btrfs-pool
- [ ] Testen op een systeem met een klassieke array en parity (`/mnt/diskN`, `/dev/mdXpY`)
- [ ] Testen met een ZFS-pool met datasets per share
- [ ] Support-link (`<Support>` in het template) invullen zodra het forumdraadje bestaat
- [ ] Screenshots in de README
- [ ] Supportdraadje op het Unraid-forum en aanmelding bij Community Applications

## Functionaliteit
- [ ] Tweetalige UI (NL/EN), standaard op browsertaal
- [ ] Schrijfacties op bestaande bestanden detecteren (fatrace `W`), gededupliceerd, zodat parity-spin-ups beter te verklaren zijn
- [ ] SAS-schijven en HBA's testen; `smartctl -n standby`-terugval valideren
- [ ] Optioneel: meldingen (Unraid-notificatie of webhook) bij spin-ups buiten een ingesteld venster
- [ ] Optioneel: per pool een "verwacht nachtvenster" instellen en afwijkingen markeren
- [ ] Export van een periode als CSV vanuit de UI

## Techniek
- [ ] Health-endpoint (`/health`) met status van poller en watcher, gebruikt door de Docker HEALTHCHECK
- [ ] `via_user_share` sneller maken bij veel processen (cache van fd-scans per seconde)
- [ ] Grote `who.csv`: frontend alleen het geselecteerde bereik laten verwerken
- [ ] Multi-arch image niet nodig (Unraid is amd64), maar documenteren
