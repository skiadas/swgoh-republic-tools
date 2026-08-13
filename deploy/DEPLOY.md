# AWS Lightsail deployment

Single $7/mo Lightsail Linux VM runs the whole service in Docker Compose:
swgoh-comlink (EA gateway), the FastAPI app, and Caddy (HTTPS).

Two paths: a **minimal smoke test** (admin token only, no domain, direct
`http://<ip>:8000`) or the **full setup** (domain + Discord login + Caddy
TLS). Both use the same compose file; for minimal, leave `SITE_DOMAIN` and the
Discord fields blank and open port 8000 on the Lightsail firewall instead of
80/443.

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
SWGOH_DISCORD_CLIENT_ID=
SWGOH_DISCORD_CLIENT_SECRET=
SWGOH_DISCORD_REDIRECT=https://reviewer.example.com/auth/discord/callback
```

- Discord: create an app in the Discord Developer Portal → OAuth2 →
  add `SWGOH_DISCORD_REDIRECT` as a redirect URL. Only the `identify` scope is
  used; no bot is needed. Leave the Discord fields blank to run without login.
- DNS: add an A record for `SITE_DOMAIN` pointing at the static IP.

## 4. Run

```bash
docker compose up -d --build
docker compose logs -f app
```

Caddy obtains a Let's Encrypt certificate automatically for `SITE_DOMAIN`.
If it's a fresh domain, allow a minute for the cert; first visit may warn.

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

## Upgrading

```bash
git pull && docker compose up -d --build
```

## Notes / limits

- EA rate limits: refresh is throttled to 4 req/s and runs one guild at a time;
  a 50-member guild takes ~30–60s. With many guilds the nightly pass spreads
  out automatically because it runs sequentially.
- The app is stateless except the `data` volume (JSON payloads + `service.db`).
  Snapshots of that volume are the only backup you need.
