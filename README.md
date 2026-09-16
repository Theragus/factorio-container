# factorio-container

Factorio as a Docker container. Always up to date.

Every Factorio **headless (dedicated server)** release is automatically
downloaded, packaged as a container image, tested and published — a GitHub
release with the upstream changelog and a link to the announcement thread on the
Factorio forums goes out alongside each image.

The headless Linux build is the only release packaged here. It is the only
Factorio build that runs without a graphical stack, which makes it the only one
that can sensibly run in a container.

---

## Quick start

```bash
docker run -d --name factorio \
  -p 34197:34197/udp \
  -v factorio-data:/factorio \
  ghcr.io/theragus/factorio-container:stable
```

On first start the container seeds `/factorio` with the configuration files
shipped in the release, generates a map and hosts it. Everything it writes —
saves, mods, config, logs — lives in `/factorio`, so replacing the container
never loses a game.

Or with Compose ([`docker-compose.yml`](docker-compose.yml)):

```bash
docker compose up -d
```

## Image tags

| Tag | Points at |
| --- | --- |
| `latest`, `stable` | the current Factorio **stable** release |
| `experimental` | the current Factorio **experimental** release |
| `2.0.77` | that exact Factorio version, forever |
| `2.0` | the newest packaged release in the 2.0 series |

Images are published to the GitHub Container Registry:

```
ghcr.io/theragus/factorio-container
```

The upstream headless build is published for `linux64` only, so the images are
`linux/amd64`.

## Configuration

Everything is optional — the defaults host a fresh map on the standard port.

| Variable | Default | What it does |
| --- | --- | --- |
| `SAVE_NAME` | `default` | Name of the map generated on first start |
| `PORT` | `34197` | UDP game port |
| `RCON_PORT` | `27015` | TCP RCON port |
| `RCON_PASSWORD` | *generated* | RCON password; when unset one is generated into `/factorio/config/rconpw` |
| `RCON_BIND` | — | Address RCON listens on |
| `BIND` | — | Address the game port binds to |
| `GENERATE_NEW_SAVE` | `true` | Create a map when the volume has no save |
| `LOAD_LATEST_SAVE` | `true` | Start the newest save instead of `SAVE_NAME` |
| `MAP_GEN_SEED` | — | Seed used when generating the first map |
| `PRESET` | — | Map generation preset used for the first map |
| `PUID` / `PGID` | `845` | Ownership of `/factorio`; match your host user for bind mounts |
| `FACTORIO_ARGS` | — | Extra flags appended to the server command line |

Files inside the volume:

```
/factorio
├── config/
│   ├── config.ini                 generated once, then yours to edit
│   ├── server-settings.json       server name, visibility, game password…
│   ├── map-gen-settings.json      only read when generating a new map
│   ├── map-settings.json          only read when generating a new map
│   ├── server-whitelist.json      create it yourself to enforce a whitelist
│   └── rconpw                     generated RCON password, if any
├── saves/
├── mods/
├── scenarios/
└── script-output/
```

To change the server name, edit `config/server-settings.json` and restart. Two
things in the seeded copy are worth knowing about:

- `visibility.public` is `true`, but listing on the public server browser also
  needs your factorio.com `username` plus a `token`. Until you fill those in,
  the server logs `Missing token` once and keeps running as a private server.
- `server-whitelist.json` is **not** created for you. The example Wube ships
  lists two of their developers, so seeding it would switch on a whitelist that
  locks everyone else out. Write the file yourself and restart to enforce one.

Any argument you pass after the image name is handed straight to the Factorio
binary, so one-off flags work too:

```bash
docker run --rm -v factorio-data:/factorio \
  ghcr.io/theragus/factorio-container:stable --version
```

`docker stop` sends `SIGTERM`, which Factorio handles: it saves the map and
exits cleanly. Give it room with `--stop-timeout 120` (or
`stop_grace_period: 2m` in Compose) if your save is large.

---

## How the automation works

```
 ┌─ daily at 05:17 UTC ───────────────────────────────────────────────┐
 │  check-releases.yml                                                │
 │    ├─ ask updater.factorio.com which headless versions exist       │
 │    ├─ ask the GitHub API which versions are already released here  │
 │    └─ emit a build matrix of everything missing                    │
 └────────────────────────────┬───────────────────────────────────────┘
                              │ one job per version, oldest first
 ┌────────────────────────────▼───────────────────────────────────────┐
 │  build-release.yml                                                 │
 │    ├─ download the headless tarball, verify its published SHA-256  │
 │    ├─ build the image                                              │
 │    ├─ smoke test it (see below) — a failure blocks the release     │
 │    ├─ push every tag to ghcr.io                                    │
 │    └─ publish a GitHub release with the changelog + forum link     │
 └────────────────────────────────────────────────────────────────────┘
```

**Where the data comes from**

| What | Source |
| --- | --- |
| List of published versions | `https://updater.factorio.com/get-available-versions` |
| Current stable / experimental | the same endpoint, with `https://factorio.com/api/latest-releases` as fallback |
| Tarball | `https://www.factorio.com/get-download/<version>/headless/linux64` |
| Checksum | `https://www.factorio.com/download/sha256sums/` |
| Release notes | `data/changelog.txt` inside the release tarball |
| Announcement link | the [Releases forum](https://forums.factorio.com/viewforum.php?f=3), with a forum search as fallback |

**No backfill.** Published state is read from this repository's own GitHub
releases, so there is no state file to drift. On a repository with no releases
yet the detector seeds only the *current* stable and experimental builds; from
then on it packages every version newer than the newest one already published.
If a run is missed, the next one catches up (oldest first, five per run by
default) rather than skipping releases.

**Nothing ships untested.** Every build must pass
[`tests/smoke-test.sh`](tests/smoke-test.sh), which starts a real container and
checks that:

1. the packaged binary reports the expected headless version;
2. a fresh volume gets seeded and a map generated;
3. the server reaches the `InGame` state and hosts the game;
4. the container healthcheck turns healthy;
5. RCON answers `/version` with the expected version;
6. the server runs as PID 1 and not as root;
7. `docker stop` shuts it down cleanly and saves the map;
8. a restart re-uses the existing save instead of regenerating one.

### Running it by hand

Package a specific version (Actions → *Check for new Factorio releases* → *Run
workflow*), or from the CLI:

```bash
gh workflow run check-releases.yml -f version=2.0.77
```

To see what the daily job would do, without touching anything:

```bash
python3 scripts/detect_versions.py --repository Theragus/factorio-container
```

### Building locally

```bash
docker build -t factorio-headless:dev \
  --build-arg FACTORIO_VERSION=2.0.77 \
  --build-arg FACTORIO_SHA256="$(python3 -c '
import sys; sys.path.insert(0, "scripts")
from factorio_api import get_sha256; print(get_sha256("2.0.77"))')" .

tests/smoke-test.sh factorio-headless:dev 2.0.77
```

### Repository layout

| Path | Purpose |
| --- | --- |
| `Dockerfile` | Two-stage build: fetch + verify the tarball, then a slim runtime |
| `docker/entrypoint.sh` | Volume seeding, map creation, privilege drop, `exec` the server |
| `docker/healthcheck.sh` | Reports healthy once the game port is bound |
| `scripts/factorio_api.py` | Client for the public Factorio release endpoints |
| `scripts/detect_versions.py` | Decides which versions still need packaging |
| `scripts/build_metadata.py` | Resolves checksum, channels and image tags for one version |
| `scripts/release_notes.py` | Renders the GitHub release notes |
| `tests/smoke-test.sh` | End-to-end container test |
| `tests/rcon.py` | Minimal RCON client used by the smoke test |
| `tests/test_release_tooling.py` | Offline unit tests for the tooling |

### First-time repository setup

The workflows need no secrets — they authenticate with the built-in
`GITHUB_TOKEN`. Two one-time settings:

- **Settings → Actions → General → Workflow permissions**: allow read and write
  (needed to create releases and push to GHCR).
- After the first publish, the package is private by default; make it public
  under **Packages → factorio-container → Package settings** if you want
  anonymous `docker pull` to work.

---

## Notes

Factorio is made by [Wube Software](https://factorio.com/). This repository only
repackages the unmodified official Linux headless build; the game itself, and
the release notes reproduced in each GitHub release, are theirs. Running a
headless server does not require a Factorio account, but connecting players do.
