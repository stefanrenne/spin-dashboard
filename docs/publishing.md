# Publishing

## Image

On every push to `main` (`latest`) and on tags `v*` (`1.2.3`), CI builds an image to
`ghcr.io/<owner>/spin-dashboard`. After the first build: *GitHub → Packages → spin-dashboard →
Package settings → Change visibility → Public*, otherwise Unraid cannot pull the image.

Release:

```bash
git tag v1.0.0 && git push --tags
```

## Community Applications

1. Fill in the correct `<Support>` link in the template.
2. Test the template by hand: *Docker → Add Container → Template URL* with the raw URL of
   `unraid/spin-dashboard.xml`.
3. Create a support thread on the Unraid forum, section *Docker Containers*. Explain what the app
   does, the permissions it needs (privileged, `--pid=host`, `/mnt` read-only, docker.sock) and why.
4. Submit the repository to Community Applications through the submission form on the forum
   (see the pinned posts in *Community Applications*). CA then indexes the `unraid/` folder.
5. Keep `<Overview>` and `<Requires>` honest about the permissions; templates with privileged
   containers are reviewed critically.

## Checklist per release

- [ ] Tests green in CI
- [ ] `completed.md` updated
- [ ] Template changes tested via *Add Container*
- [ ] Tag set, image built, package public
