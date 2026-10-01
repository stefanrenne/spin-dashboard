# Publiceren

## Image

CI bouwt bij elke push naar `main` (`latest`) en bij tags `v*` (`1.2.3`) een image naar
`ghcr.io/<owner>/spin-dashboard`. Na de eerste build: *GitHub → Packages → spin-dashboard →
Package settings → Change visibility → Public*, anders kan Unraid het image niet ophalen.

Release:

```bash
git tag v1.0.0 && git push --tags
```

## Community Applications

1. Vul in het template de juiste `<Support>`-link in.
2. Test het template handmatig: *Docker → Add Container → Template URL* met de raw-URL van
   `unraid/spin-dashboard.xml`.
3. Maak een supportdraadje in het Unraid-forum, sectie *Docker Containers*. Vermeld wat de app
   doet, de vereiste rechten (privileged, `--pid=host`, `/mnt` read-only, docker.sock) en waarom.
4. Meld de repository aan bij Community Applications via het aanmeldformulier op het forum
   (zie de vastgezette posts in *Community Applications*). CA indexeert daarna de `unraid/`-map.
5. Houd `<Overview>` en `<Requires>` eerlijk over de rechten; templates met privileged
   containers worden kritisch bekeken.

## Checklist per release

- [ ] Tests groen in CI
- [ ] `completed.md` bijgewerkt
- [ ] Template-wijzigingen getest via *Add Container*
- [ ] Tag gezet, image gebouwd, package publiek
