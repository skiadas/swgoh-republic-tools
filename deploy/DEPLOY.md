# AWS Lightsail deployment

Single $7/mo Lightsail Linux VM runs the whole service in Docker Compose:
swgoh-comlink (EA gateway), the FastAPI app, and Caddy (HTTPS).

Two paths: a **minimal smoke test** (admin token only, no domain, app reachable
directly on port 80) or the **full setup** (domain + Discord login + Caddy
TLS). For minimal, leave `SITE_DOMAIN` and the Discord fields blank, open port
**80** (not 443) on the Lightsail firewall, and run with the compose override:

```bash
docker compose -f compose.yaml -f compose.minimal.yaml up -d
```

`compose.minimal.yaml` maps the app to host port 80. Caddy is gated behind a
`web` compose profile, so it only starts in the full setup (`--profile web`);
no `Caddyfile` is needed for the minimal path.

## 1. Create the instance

- Lightsail → Create instance → Linux/Unix → **Ubuntu 24.04 LTS**, $7 bundle
  (1 GB RAM / 40 GB SSD / 2 TB transfer). Enable a static IP and attach it.
- Note the public IPv4. Open a shell (browser SSH or your key).

## 2. Install Docker + Compose plugin

```bash
curl -fsSL https://get.docker.com | sh
sudo apt-get install -y docker-compose-plugin
sudo usermod -aG docker ubuntu   # re-login or use sudo docker ...
```

## 3. Get the code and configure

The repo and its container image are public — no GitHub or registry login.
Fetch only the files the box needs (no source code):

```bash
mkdir -p ~/swgoh-reviewer && cd ~/swgoh-reviewer
BASE=https://raw.githubusercontent.com/skiadas/swgoh-republic-tools/main
for f in compose.yaml compose.minimal.yaml .env.example Caddyfile diun.yml diun-watch.yml; do
  curl -fsSL -o "$f" "$BASE/$f"
done
cp .env.example .env
$EDITOR .env      # set secrets (see below)
```

Note: `~/swgoh-reviewer` becomes the compose project directory. Everything
below assumes you run compose from there.

`.env`:

```dotenv
SWGOH_ADMIN_TOKEN=<long random string>
SWGOH_APP_SECRET=<long random string>
SWGOH_ADMIN_DISCORD_ID=<your discord user id>
SITE_DOMAIN=reviewer.example.com
SWGOH_IMAGE_TAG=latest
SWGOH_APP_DIR=~/swgoh-reviewer   # optional; defaults to the project dir
SWGOH_DISCORD_CLIENT_ID=
SWGOH_DISCORD_CLIENT_SECRET=
SWGOH_DISCORD_REDIRECT=https://reviewer.example.com/auth/discord/callback
```

- Discord: create an app in the Discord Developer Portal → OAuth2 →
  add `SWGOH_DISCORD_REDIRECT` as a redirect URL. Only the `identify` scope is
  used; no bot is needed. Leave the Discord fields blank to run without login.
- DNS: add an A record for `SITE_DOMAIN` pointing at the static IP.
- `SWGOH_IMAGE_TAG` picks which container image to run (default `latest`);
  set it to a git sha or a `v*` tag to pin/roll back (see "Versioning").
- `SWGOH_APP_DIR` is the host path Diun binds into itself to run
  `docker compose`; it defaults to the project directory (`.`), so you only
  set it if you run compose from somewhere else.

## 4. Run

```bash
docker compose -f compose.yaml -f compose.minimal.yaml up -d app   # minimal, no domain; pulls the GHCR image
docker compose logs -f app
```

(The full setup drops the minimal override and adds the `web` profile so Caddy
starts: `docker compose --profile web up -d`. It also requires fetching the
`Caddyfile` and pointing your domain at the static IP. No `--build` on the box —
the image is built in CI and pulled from `ghcr.io/skiadas/swgoh-republic-tools`.)

## 5. Onboard the first guild

Sign in at `https://reviewer.example.com/admin/login` with the admin token
(24h session cookie), then use the admin page to register the guild (guild id
or any member's ally code). The admin token is only ever submitted via that
login form — it is never put in a URL.

## 6. Nightly refresh & backups

- `SWGOH_NIGHTLY=1` (compose default) makes the app refresh every enabled
  guild nightly at `SWGOH_REFRESH_HOUR` UTC, throttled (4 req/s), one guild at
  a time.
- Backups: Lightsail snapshots, or a nightly tarball of the `data` volume:

```bash
docker run --rm -v swgoh-reviewer_data:/data -v $PWD:/backup alpine \
  tar czf /backup/swgoh-data-$(date +%F).tgz -C /data .
```

Keep ~7 of these; copy off-instance if you want off-box redundancy.

## Versioning & rollback

- Every push to `main` builds `ghcr.io/skiadas/swgoh-republic-tools:<sha>`
  and updates `:latest`. A `v*` git tag (e.g. `git tag v1.0.0 && git push --tags`)
  also pushes a `<tag>` image.
- The box runs `:latest` by default. To pin or roll back, set
  `SWGOH_IMAGE_TAG` in `.env` to a git sha or tag and restart:
  ```bash
  SWGOH_IMAGE_TAG=<sha-or-tag> docker compose --profile web up -d app
  ```
- The app reports its version at `GET /healthz`.

## Upgrading (self-update via Diun)

The compose stack includes **Diun**, which watches `ghcr.io/skiadas/swgoh-republic-tools:latest`
every 10 minutes (File provider — it polls the registry directly, no Docker
daemon API involved) and, on an update, runs its **Script notifier** to pull
the new image and recreate the app container:

```bash
docker compose pull app && docker compose up -d app
```

That command runs inside the Diun container with the host Docker socket and
`docker` CLI mounted, working in the project directory. A ~2–5s restart blip
is expected; the `data` volume is untouched.

```bash
# verify Diun is healthy
docker compose --profile web exec diun diun healthcheck
docker compose --profile web logs diun --tail 20
```

> **Project name:** compose.yaml pins `name: swgoh-reviewer`. If the box's
> existing stack was started before this pin existed (i.e. its project name
> was the directory name), migrating requires a brief stop first:
> ```bash
> docker compose --profile web down          # uses the OLD project name
> # ... fetch the updated files (step 3) ...
> docker compose --profile web up -d
> ```
> `down` keeps volumes, so guild data survives.

Compose/config changes are **not** pushed to the box automatically — after a
compose change, re-fetch the file (fetch-only setup) and recreate:

```bash
cd ~/swgoh-reviewer
BASE=https://raw.githubusercontent.com/skiadas/swgoh-republic-tools/main
curl -fsSL -o compose.yaml "$BASE/compose.yaml"
docker compose --profile web up -d
```

## Memory & swap

On the $7 box (1GB RAM) we recommend a 2GB swapfile so the OOM killer stays
away during nightlies, and the compose services carry hard memory limits
(RAM + swap ceiling so a container can spill into swap):

| service | mem_limit | memswap_limit |
|---|---|---|
| app | 512m | 1g |
| comlink | 384m | 768m |
| caddy | 128m | 256m |
| diun | 128m | 256m |

```bash
# swap setup (once)
sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/90-swappiness.conf && sudo sysctl vm.swappiness=10
```

Note: `mem_limit` without `memswap_limit` would disable swap for that
container; both are set so transient spikes spill into the host swap.

## Notes / limits

- EA rate limits: refresh is throttled to 4 req/s and runs one guild at a time;
  a 50-member guild takes ~30–60s. With many guilds the nightly pass spreads
  out automatically because it runs sequentially.
- The nightly refresh runs on a thread inside the app and does not
  meaningfully affect page serving; all artifact writes are atomic
  (temp-file + `os.replace`), so readers never see a partial file.
- The app is stateless except the `data` volume (JSON payloads + `service.db`).
  Snapshots of that volume are the only backup you need.
