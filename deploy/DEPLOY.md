# AWS Lightsail deployment

Single $7/mo Lightsail Linux VM runs the whole service in Docker Compose:
swgoh-comlink (EA gateway), the FastAPI app, and Caddy (HTTPS).

Two paths: a **minimal smoke test** (admin token only, no domain, app reachable
directly on port 80) or the **full setup** (domain + Discord login + Caddy
TLS). For minimal, leave `SITE_DOMAIN` and the Discord fields blank, open port
**80** (not 443) on the Lightsail firewall, and run with the compose override:

```bash
docker compose -f compose.yaml -f compose.minimal.yaml up -d --build app
```

`compose.minimal.yaml` maps the app to host port 80 and keeps Caddy off. At the
full setup, drop the override file and let Caddy take over 80/443.

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

```bash
git clone https://github.com/skiadas/swgoh-republic-tools.git swgoh-reviewer
cd swgoh-reviewer
cp .env.example .env
$EDITOR .env      # set secrets (see below)
```

`.env`:

```dotenv
SWGOH_ADMIN_TOKEN=<long random string>
SWGOH_APP_SECRET=<long random string>
SWGOH_ADMIN_DISCORD_ID=<your discord user id>
SITE_DOMAIN=reviewer.example.com
SWGOH_IMAGE_TAG=latest
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

## 4. Run

```bash
docker compose -f compose.yaml -f compose.minimal.yaml up -d app   # minimal, no domain; pulls the GHCR image
docker compose logs -f app
```

(The full setup uses plain `docker compose up -d` once the domain is
in place; Caddy then terminates HTTPS on 80/443. No `--build` on the box —
the image is built in CI and pulled from `ghcr.io/skiadas/swgoh-republic-tools`.)

## 5. Onboard the first guild

```bash
# from the app container's data root:
curl -s -X POST "https://reviewer.example.com/admin/guilds?guild_id=<guildId>&token=$SWGOH_ADMIN_TOKEN"
# or use the web admin UI at /admin
```

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
  SWGOH_IMAGE_TAG=<sha-or-tag> docker compose -f compose.yaml -f compose.minimal.yaml up -d app
  ```
- The app reports its version at `GET /healthz`.

## Upgrading

```bash
git pull && docker compose -f compose.yaml -f compose.minimal.yaml up -d app   # pulls the new image
```
(Use the full compose command without the minimal override once the domain is set.)

## Notes / limits

- EA rate limits: refresh is throttled to 4 req/s and runs one guild at a time;
  a 50-member guild takes ~30–60s. With many guilds the nightly pass spreads
  out automatically because it runs sequentially.
- The app is stateless except the `data` volume (JSON payloads + `service.db`).
  Snapshots of that volume are the only backup you need.
