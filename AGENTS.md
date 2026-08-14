# AGENTS.md

Guidance for working in this repository. It downloads and reports on player
data for STAR WARS: Galaxy of Heroes (SWGOH) via a local `swgoh-comlink`
service (live `/player` and `/guild` calls) plus static game-data files from
the `swgoh-utils/gamedata` repo. No swgoh.gg scraping. Everything runs with
`uv run python <script>`; tests with `uv run pytest`.

## Ground rules

- **Game data comes from the static repo, not comlink.** `StaticGameData`
  (`swgoh_reviewer/static_gamedata.py`) is a duck-typed drop-in for the
  game-data methods of the comlink-python API, served from the
  `swgoh-utils/gamedata` GitHub repo (env `SWGOH_GAMEDATA_BASE`; set to `""`
  to fall back to comlink). Keep comlink to the live `/player` and `/guild`
  calls only.
- **Don't try to raise comlink's memory.** Its heap cap is baked into the
  bundled Node binary; `NODE_OPTIONS`/`mem_limit` can't change it (that's why
  game data comes from the static repo).
- **All artifact writes go through `swgoh_reviewer/io.py:atomic_write_text`**
  (temp file + `os.replace`) so readers never see a partial file.
- **All logic lives in `swgoh_reviewer/`; top-level `*.py` are thin CLI
  wrappers** that parse args and call `swgoh_reviewer.<module>.main()`.
- **Relic values in any output are the in-game relic level** `max(0, raw - 2)`
  (0 = no relic, max R10). Raw game/player values (`relic.currentTier`,
  `minimumRelicTier`, `unitRelicTier`) sit on an offset scale (1–2 = no relic,
  3 = R1, ..., 12 = R10). Never display a raw tier — raw 11 was once shown as
  "R11", which doesn't exist in game.
- **The job runner's lock must stay reentrant** (`threading.RLock`):
  `refresh_guild` calls `regen` while holding it; a plain `threading.Lock`
  deadlocks the worker (the fetch completes but pages never regenerate).
- **Jinja2 pages: keep JS braces single** (`{{ }}` are Jinja's only).
- **Faction `pool` by tag matches characters only** (ships excluded).

## Common operations

**Tools** (`uv run python <tool>`): `fetch_guild.py` (fetch/refresh a guild),
`guild_summary.py` (offline summary rebuild), `build_caches.py` (rebuild game
caches), `squad_report.py` (squad report), `render_report.py` (HTML dashboard),
`rote.py` (TB doc), `rote_ops.py` (op fills), `rote_calc.py` (calculator page),
`start_comlink.sh` (comlink in Docker).

- **Run / test / compile:**
  ```bash
  uv run python <script>
  uv run pytest
  uv run python -m py_compile *.py swgoh_reviewer/*.py
  ```
- **Start comlink (Docker):** `./start_comlink.sh` → `http://localhost:3200`.
  The prebuilt comlink *binary* is broken on macOS (pkg/V8 mismatch) — use the
  container.
- **Fetch / refresh a guild:** `fetch_guild.py <allycode>` (or `--guild-id`)
  streams each member roster through comlink, writes the manifest + summary
  (`data/guilds/<id>.json`, `<id>.summary.json`) and discards raw rosters.
  Default 4 req/s (`--max-rps`); `--limit N` for a small test batch.
- **Rebuild game-data caches after a game update:** `fetch_guild.py
  --refresh-game`, `guild_summary.py --refresh-game`, `build_caches.py`, or
  `rote.py --refresh` — each re-checks `allVersions.json` against the cached
  stamp and re-downloads only when the game changed. None need comlink.
- **Regenerate pages from caches:** `squad_report.py <guild_id>` →
  `data/guilds/<id>.squads.json`; `render_report.py <guild_id>` →
  `.squads.html`; `rote_calc.py <guild_id>` → `.calculator.html`. The server
  admin's "Regenerate pages" runs all three.
- **Rebuild a summary offline:** `guild_summary.py <guild_id>` from
  `data/<allyCode>.json` raw rosters + caches (dev tool).
- **Run the web app locally:** `uv run python server/app.py` (reads `SWGOH_PORT`,
  default 8000 — the repo's gitignored `.env` sets it to 8500) or
  `uv run uvicorn server.app:app --port <port>`.
- **Deploy:** push to `main` → CI builds the image → the box's `update-app.sh`
  cron (every 10 min) pulls + recreates the app. See `deploy/DEPLOY.md` for
  setup and diagnostics.
- **Verify a change:** the compile/test checks above; report sanity via
  `uv run python squad_report.py <guild_id> --player <allycode>`; page JS via
  `node --check` on the second `<script>` block.

## How the pipeline is structured

`fetch_guild.py` resolves the guild (from an ally code or id), builds the
game-data caches from the static repo, then fetches each member's roster via
comlink, reducing each to a summary entry on the fly — nothing per-player is
persisted beyond the summary. The manifest carries `memberLevel` roles
(2=member, 3=officer, 4=leader).

Roster matching notes:
- Unit matching is by display name (unique per unit in practice); `baseId` is
  recorded in outputs for precision. Some units share a display name across
  baseIds (e.g. "The Mandalorian (Beskar Armor)" journey variants) — harmless
  for matching.
- The static unit catalog is the canonical roster set (410 baseIds); comlink's
  `/data` also returned ~111 event/raid/journey variants (e.g.
  `THEMANDALORIANBESKARARMOR_JOURNEY_EVENT`) that never appear in player
  rosters. `names.json` is likewise the 410-unit set.

## Data layout (`data/`)

- `data/<allyCode>.json` — full player rosters, kept only by
  `guild_summary.py` (offline rebuild); `fetch_guild.py` no longer writes them.
- `data/names.json` — `baseId -> display name` for all units (410 set).
- `data/game/` — compact caches: `units.json` (per-unit `combatType`,
  `categories`, `leader`, `name`, `factions` projection), `categories.json`,
  `localization.json`, `factions.json`. `localization.json` is read only by
  `tb.py`. skills.json was dropped (nothing reads ability/zeta/omicron data).
- `data/game/static/` — raw `swgoh-utils/gamedata` downloads for offline
  rebuilds: `all-versions.json` (version stamp), `units.json.br`,
  `category.json`, `Loc_ENG_US.txt.json.br`, plus the TB sources
  `territoryBattleDefinition.json`, `campaign.json.br`, `displayableEnemy.json`,
  `table.json`, `swgoh_rote_operations.json` (re-acquired on game version
  changes).
- `data/guilds/<guildId>.json` — guild manifest (member list, GP, statuses,
  roles).
- `data/guilds/<guildId>.summary.json` — per-member roster summary (units:
  name, baseId, combatType, gearLevel, relicLevel, rarity, factions, leader).
- `data/guilds/<guildId>.squads.json` / `.squads.html` — squad report + dashboard.
- `data/rote/<tbId>.json` / `.md` — merged TB doc + dump built by `rote.py`.
- `data/rote/<planet>.ops.json` / `.ops.html` — op-fill planner output
  (`rote_ops.py`).
- `data/guilds/<guildId>.calculator.html` — ROTE calculator page
  (`rote_calc.py`). Full model: `docs/rote-calculator.md`.

## Tools & conventions

### ROTE star calculator (`rote_calc.py`)

Self-contained Jinja2 HTML page (`HTML_TEMPLATE`) with data inlined as
`const DATA = {...}`; app logic is the second `<script>` block (single JS
IIFE). Inputs: `data/rote/t05D.json` planets (star thresholds, op platoon
rewards, CM max from each mission's `pointsPerWave` × `CM_MULTIPLIER`) plus
`data/guilds/<guildId>.summary.json` guild GP. Plans live in **per-guild**
`localStorage` keys (no server storage yet); a "Share" button copies a URL with
the plan payload embedded (`?plan=<base64url>`), and opening such a URL loads it
as an editable "Shared" plan. Verify the JS with `node --check` on the second
`<script>` block; jsdom sanity: 100% CM → 47 stars no unlocks / 54 both
specials; 50% → 43; 30% → 41. Full model (per-day aggregate `compute()`,
UI/state, optimizer): `docs/rote-calculator.md`.

### Op-fill planning (`rote_ops.py`)

- Every unit listed in a planet's op is a required fill (6 platoons × 15 = 90).
  A member qualifies at `relic >= op relic` (characters) or 7 stars (ships),
  fills each distinct unit at most once, capped at 10 units per planet.
  Greedy: hardest fills first (fewest qualifiers), then lowest qualifying
  relic, then fewest assignments so far, then highest GP. "Closest" = owners
  below the requirement (top 5).
- Requires `rarity` in the guild summary (ships), so re-run `guild_summary.py`
  after a roster refresh.

### Territory Battle docs (`rote.py`)

- Built entirely from the cached static gamedata (no comlink, no swgoh.gg):
  `territoryBattleDefinition`, the `campaign` TB slice (`get_campaign_slice` —
  targeted extraction of the t05D entry from the ~53MB `campaign.json.br`, so
  it never parses the whole thing), `table` (points-per-wave),
  `displayableEnemy`, and `swgoh_rote_operations` (ops/platoons + relic tiers +
  rewards).
- Ops merge onto planets by `linkedConflictId` == conflict `zoneId`; platoons
  are numbered by their position in the ops file (their `tb3-platoon-N` ids run
  the other way). All relic values (op requirement, recon, deploy minRelic) are
  normalized with `max(0, raw - 2)`. Op names are shortened ("Coruscant
  Operation" → "Coruscant Op").
- Requires the static gamedata source (errors if `SWGOH_GAMEDATA_BASE=""`).
  `--refresh` re-checks the static repo for updates.
- The old `data/rote/*.json` comlink raws are stale/unused.

### Squads requirements

- `squads.json` — squad definitions (categories → squads: `mode`, `minRelic`,
  `size`, `required`, optional `pool` = names list or `{"tag": "Faction"}`,
  `poolCount`). Two modes: `minRelic` (hard relic floor) and `commonRelic`
  (raid-style: reports the best common relic level across the `thresholds`
  set, default `[0,1,3,5,7,8,9]`). Full format: **`squads.md`**; machine spec:
  **`squads.schema.json`**. Categories may be empty.
- `squad_report.py` validates against the schema at load and warns about unit
  names / faction tags never seen in game data.
- `commonRelic` results carry `commonRelic`/`nextThreshold`/`bottlenecks`
  instead of `complete`/`gap`; a missing required unit yields `commonRelic:
  null`. Matrix/HTML branch on `mode` accordingly.

### Web service (`server/`)

FastAPI app serving each registered guild's generated pages, with a token-gated
admin. Run locally with `uv run uvicorn server.app:app`. Env config:
- `SWGOH_DATA_ROOT` / `SWGOH_COMLINK` / `SWGOH_GAMEDATA_BASE` — paths (see
  Ground rules).
- `SWGOH_ADMIN_TOKEN` — admin token (required); `SWGOH_APP_SECRET` — session
  cookie secret; `SWGOH_ADMIN_DISCORD_ID` — your Discord id = admin user.
- `SWGOH_NIGHTLY=1` — nightly refresh loop (`SWGOH_REFRESH_HOUR`, default 4
  UTC); `SWGOH_PORT` — uvicorn port (default 8000).
- `SWGOH_DISCORD_CLIENT_ID` / `SECRET` / `REDIRECT` — Discord OAuth login;
  `SWGOH_COOKIE_SECURE=1` behind HTTPS.

Layout: `server/db.py` (SQLite: guild registry incl. per-guild
`squads_json` + `last_refresh`, `discord_links`, `job_log`; the legacy
`tb_id`/`enabled` columns are unused), `server/jobs.py`
(JobRunner — regen offline, refresh via comlink, nightly loop; serialized by
the reentrant lock; admin-triggered jobs are **enqueued** and run on a
background worker so requests return immediately with a 303, status visible in
`job_log`), `server/auth.py` (Discord OAuth + signed cookies + roster-derived
roles), `server/app.py` (routes). Read access is open at
`/g/<id>/{report,calc}`; `/g/<id>/squads` needs an officer/leader
role (or admin); `/admin*` needs the signed 24h admin cookie (token entered at
`/admin/login`, never a URL). Every registered guild is public and refreshed
nightly; removing a guild (admin → Remove guild, confirmed) deletes its DB row
and `data/guilds/<id>.*` files. Discord links (`discord_id → allycode`) are made
by an admin; roles come from the manifest's `memberLevel`.
Per-guild custom `squads_json` is materialized to
`data/guilds/<id>.squads-config.json` and passed to `squad_report`.
Tests: `tests/test_server.py` (TestClient, no comlink).

## When you change X, also do Y

- **Add or edit a page:** it's a Jinja2 template with a single `<script>` block
  (JS braces kept single), data inlined as `const DATA = {...}`, written via
  `atomic_write_text`, and linked from `server/app.py` routes; regenerate the
  page to verify. Render in jsdom and inspect each tab (matrix rows =
  players+2, cells = players×squads).
- **Change the game-data cache format:** update the staleness guard
  (`_units_projection_ok` in `gamecache.py`) so a pre-projection cache is
  rebuilt, then rebuild caches (`build_caches.py`).
- **Add a relic-bearing field:** normalize with `max(0, raw - 2)` and
  sanity-check against the calibration anchor: player `679577173`'s `GLLEIA`
  has raw `relic.currentTier` 11 == R9.
- **Touch the job runner:** keep the lock reentrant (`threading.RLock`).
- **Edit `calc.py` JS:** `node --check` the second `<script>` block; jsdom
  sanity 47★/54/43/41.
- **Change the ops/TB merge:** ops attach by `linkedConflictId`; platoons are
  numbered by position in `swgoh_rote_operations.json`, not by their id suffix.
