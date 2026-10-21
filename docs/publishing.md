# Publishing

## Image

On every push to `main` (`latest`) and on tags `v*` (`1.2.3`), CI builds an image to
`ghcr.io/<owner>/spin-dashboard`. Every Monday it also rebuilds `latest` from `main` with a freshly pulled base
image, so security fixes of Python's Debian base and of `fatrace`, `hdparm` and `smartmontools` reach the image
without a code change. Dependabot (`.github/dependabot.yml`) opens weekly PRs for the actions, the test tools
and the base image, one PR per ecosystem (grouped). On a PR, CI runs the tests and builds the image without pushing it; when that passes,
`.github/workflows/dependabot-automerge.yml` merges the PR (squash, only the commit CI tested) and starts CI
on `main`, which builds and pushes `latest`. A PR whose tests or image build fail stays open for a look. After the first build: *GitHub → Packages → spin-dashboard →
Package settings → Change visibility → Public*, otherwise Unraid cannot pull the image.

The image is built for `linux/amd64` only. Unraid runs on x86-64, so a multi-arch image (arm64)
would only add build time; `fatrace` and the host paths are the same either way. Add
`linux/arm64` to `platforms` in `.github/workflows/ci.yml` if that ever changes.

Release:

1. In `CHANGELOG.md`, turn `## [Unreleased]` into `## [X.Y.Z] - YYYY-MM-DD` and start a new empty
   `## [Unreleased]` above it.
2. Commit, then tag and push:

```bash
git tag vX.Y.Z && git push origin main vX.Y.Z
```

CI builds `ghcr.io/<owner>/spin-dashboard:X.Y.Z` with `SPINDASH_VERSION=X.Y.Z` in it (a build of `main` gets
`main-<short sha>`). The version is in `/health` (`"version"`), in the start log line and at the bottom of
the page, with a link to the changelog. `latest` keeps following `main`.

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
- [ ] `completed.md` updated, `CHANGELOG.md` has the version and its date
- [ ] Template changes tested via *Add Container*
- [ ] Tag set, image built, package public
