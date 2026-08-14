# AGENTS.md

Guidance for working in this repository. It downloads and reports on player
data for STAR WARS: Galaxy of Heroes (SWGOH) using a self-hosted
`swgoh-comlink` service (a local gateway to EA's read-only game APIs) plus the
static game-data files from the `swgoh-utils/gamedata` repo. No swgoh.gg
scraping.

## Pipeline

All logic lives in the `swgoh_reviewer/` package; the top-level `*.py`
scripts are thin CLI wrappers. Data paths are env-driven (`SWGOH_DATA_ROOT`,
`SWGOH_COMLINK`, `SWGOH_GAMEDATA_BASE` — see `swgoh_reviewer/config.py`).

```
start_comlink.sh            run swgoh-comlink (Docker) once
fetch_guild.py <allycode>   fetch a guild and write its summary, streaming
guild_summary.py <guild_id> rebuild a summary from existing raw rosters (dev)
build_caches.py             build game-data caches from the static gamedata repo
squad_report.py <guild_id>  evaluate squads.json requirements vs the summary
render_report.py <guild_id> emit a self-contained HTML dashboard of the report
rote.py [<tb_id>]           document a Territory Battle (default ROTE t05D)
rote_ops.py <planet>        plan a planet's op fills against the guild roster
rote_calc.py                interactive ROTE day-by-day star calculator page
```

`fetch_guild.py` streams: each member roster is fetched, reduced to its summary
entry, and the raw payload is discarded — no `data/<allyCode>.json` raw rosters
are persisted (was ~330MB/guild). The guild manifest carries `memberLevel`
roles; the raw `.guild.json` response is not kept.

### Game data: static source, not comlink

Game-data caches (`units`, `categories`, `localization`, `names`) are built
from the `swgoh-utils/gamedata` GitHub repo via `swgoh_reviewer/static_gamedata.py`
(`StaticGameData`, a duck-typed drop-in for the game-data methods of the
comlink-python API). comlink is used only for the live `/player` and `/guild`
calls — see the comlink gotcha for why. Set `SWGOH_GAMEDATA_BASE=""` to fall
back to comlink for game data.

## Setup / running

- Everything runs with `uv run python <script>` (Python 3.10+; deps in
  `pyproject.toml`, locked by `uv.lock`). Tests: `uv run pytest`.
- The only live network dependency is `swgoh-comlink`, started via
  `./start_comlink.sh` (Docker, listens on `http://localhost:3200`), used for
  the `/player` and `/guild` calls. Game-data caches come from the static
  `swgoh-utils/gamedata` repo (see below) and are downloaded once, then
  cached under `data/game/static/` for offline use.
- macOS: the prebuilt comlink *binary* is broken on this machine (pkg/V8
  mismatch) — use the Docker container.

## Data layout (`data/`)

- `data/<allyCode>.json` — full player rosters kept only by `guild_summary.py`
  (offline rebuild); `fetch_guild.py` no longer writes these.
- `data/names.json` — `baseId -> display name` cache for all units.
- `data/game/` — compact game-data caches: `units.json`, `categories.json`,
  `localization.json`, `factions.json` (skills.json was dropped — nothing reads
  ability/zeta/omicron data anymore). Built from the static gamedata repo, not
  comlink. `units.json` entries carry the precomputed `name` + `factions`
  (visible localized category names) projection, so summary building only loads
  that small file — the large `localization.json` is read only by `tb.py`.
  `factions.json` is the sorted list of visible localized category names used
  by `squad_report.py` for tag validation.
- `data/game/static/` — raw swgoh-utils/gamedata downloads cached for offline
  rebuilds: `all-versions.json` (version stamp), `units.json.br`,
  `category.json`, `Loc_ENG_US.txt.json.br`, plus the Territory Battle sources
  `territoryBattleDefinition.json`, `campaign.json.br`, `displayableEnemy.json`,
  `table.json`, `swgoh_rote_operations.json` (all re-acquired automatically
  when the game version stamp changes).
- `data/guilds/<guildId>.json` — guild manifest (member list, GP, statuses,
  `memberLevel` roles).
- `data/guilds/<guildId>.summary.json` — compact per-member roster summary
  (units: name, baseId, combatType, gearLevel, relicLevel, rarity, factions,
  leader). Written compactly (~3.6MB vs the old ~25MB).
- `data/guilds/<guildId>.squads.json` — squad report (`bySquad` + `byPlayer`).
- `data/guilds/<guildId>.squads.html` — generated dashboard.
- `data/rote/<tbId>.json` — merged TB doc (phases -> planets -> missions with
  deploy requirements + ops with platoons/units) built by `rote.py`; planets
  carry `starThresholds`, missions carry `waves` + `pointsPerWave`.
- `data/rote/<tbId>.md` — human-readable TB dump.
- `data/rote/<planet>.ops.json` / `.ops.html` — op-fill planner output for one
  planet (`rote_ops.py`): which fills are covered, missing, closest owners,
  member assignments.
- `data/guilds/<guildId>.calculator.html` — interactive day-by-day star
  calculator for that guild (`rote_calc.py`), a self-contained HTML page.
  Full model: `docs/rote-calculator.md`.

## ROTE star calculator (`rote_calc.py`)

Self-contained Jinja2 HTML page (`HTML_TEMPLATE`) with data inlined as
`const DATA = {...}`; the app logic is the second `<script>` block (a single
JS IIFE). Inputs: `data/rote/t05D.json` planets (star thresholds, op platoon
rewards, CM max from each mission's `pointsPerWave` × `CM_MULTIPLIER`) plus
`data/guilds/<guildId>.summary.json` guild GP. Verify the JS with `node --check`
on the second `<script>` block; jsdom sanity reference: 100% CM → 47 stars no
unlocks / 54 both specials; 50% → 43; 30% → 41. Full model (per-day aggregate
`compute()`, UI/state, optimizer): `docs/rote-calculator.md`.

## Op-fill planning (`rote_ops.py`)

- Every unit listed in a planet's op is a required fill (6 platoons x 15 = 90).
  A member qualifies at `relic >= op relic` (characters) or 7 stars (ships),
  fills each distinct unit at most once, capped at 10 units per planet.
  Greedy: hardest fills first (fewest qualifiers), then lowest qualifying
  relic, then fewest assignments so far (spreads the load), then highest GP.
  "Closest" = owners below the requirement (top 5).
- Requires `rarity` in the guild summary (ships), so `guild_summary.py` must be
  re-run after a roster refresh.

## Territory Battle docs (`rote.py`)

- Built entirely from the cached static gamedata (no comlink, no swgoh.gg):
  `territoryBattleDefinition` (structure, planets, star thresholds, zones),
  the `campaign` TB slice (`get_campaign_slice` — a targeted extraction of the
  t05D entry from the ~53MB campaign.json.br, so it never parses the whole
  thing into memory), `table` (points-per-wave), `displayableEnemy`, and
  `swgoh_rote_operations` (the ops/platoon unit lists + relic tiers + rewards,
  replacing the old manual swgoh.gg page saves).
- Ops merge onto planets by `linkedConflictId` == conflict `zoneId`; platoons
  are numbered by their position in the ops file (their `tb3-platoon-N` ids run
  the other way). All relic values (op requirement, recon, deploy minRelic) are
  normalized to the in-game level with `max(0, raw - 2)`. Op names are
  shortened for display ("Coruscant Operation" -> "Coruscant Op").
- `--refresh` re-checks the static gamedata for updates (re-acquires the TB
  files when the game version changes).

## Squads requirements

- `squads.json` — squad definitions (categories -> squads: `mode`, `minRelic`,
  `size`, `required`, optional `pool` = names list or `{"tag": "Faction"}`,
  `poolCount`). Two modes: `minRelic` (hard relic floor) and `commonRelic`
  (raid-style: reports the best common relic level across the `thresholds`
  set, default `[0,1,3,5,7,8,9]`). Full format: **`squads.md`**; machine spec:
  **`squads.schema.json`**. Categories may be empty.
- `squad_report.py` validates `squads.json` against the schema at load and
  warns about unit names / faction tags never seen in game data.

## Refresh flags / cache invalidation

- `fetch_guild.py` always fetches fresh (streaming; nothing to skip).
- `fetch_guild.py --limit N` for a small test batch; `--max-rps` (default 4)
  throttles to stay under EA's caps (~20 req/s total, ~100 for /player).
- `fetch_guild.py --refresh-game` and `guild_summary.py --refresh-game`
  re-check `allVersions.json` against the cached stamp and re-download +
  rebuild `data/game/` caches only when the game changed. `build_caches.py`
  does the same without a guild. `rote.py --refresh` re-acquires the TB
  collections the same way. None of these need comlink.

## Web service (`server/`)

FastAPI app serving each registered guild's generated pages, with a
token-gated admin. Run locally with `uv run uvicorn server.app:app` (or
`uv run python server/app.py`). Env config:

- `SWGOH_DATA_ROOT` — data directory (shared with the CLI tools).
- `SWGOH_COMLINK` — swgoh-comlink URL (fetch/refresh only).
- `SWGOH_GAMEDATA_BASE` — swgoh-utils/gamedata base URL for game-data caches
  (set to `""` to fall back to comlink for game data).
- `SWGOH_ADMIN_TOKEN` — bearer/query token for `/admin*` (required for admin).
- `SWGOH_NIGHTLY=1` — enable the nightly refresh loop (`SWGOH_REFRESH_HOUR`,
  default 4 UTC) which fetches each enabled guild via comlink then regenerates
  its pages.
- `SWGOH_PORT` — uvicorn port (default 8000).
- `SWGOH_DISCORD_CLIENT_ID` / `SWGOH_DISCORD_CLIENT_SECRET` /
  `SWGOH_DISCORD_REDIRECT` — enable Discord OAuth login (no passwords).
- `SWGOH_APP_SECRET` — secret for signed session cookies (set in prod; random
  per-process fallback in dev). `SWGOH_ADMIN_DISCORD_ID` — your Discord id =
  admin user. `SWGOH_COOKIE_SECURE=1` behind HTTPS.

Layout: `server/db.py` (SQLite: guild registry incl. per-guild `tb_id`/
`squads_json`/`enabled`, `discord_links`, `job_log`), `server/jobs.py`
(JobRunner — regen offline, refresh via comlink, nightly loop; serialized by a
lock; admin-triggered jobs are **enqueued** and run on a background worker so
requests return immediately with a 303 redirect, with status visible in
`job_log`), `server/auth.py` (Discord OAuth + signed cookies + roster-derived
roles), `server/app.py` (routes). Read access is open for registered guilds at
`/g/<id>/{report,calc}`; `/g/<id>/{squads,settings}` require an officer/leader
roster role (or admin); `/admin*` is gated by a signed 24h admin session
cookie obtained at `/admin/login` (token in the POST body, never a URL).
Discord links (`discord_id → allycode`) are created by an admin; roles come
from the guild manifest's `memberLevel` (2=member, 3=officer, 4=leader).
Per-guild custom `squads_json` is materialized to
`data/guilds/<id>.squads-config.json` and passed to `squad_report`.
Tests: `tests/test_server.py` (TestClient, no comlink).

All artifact writes (summaries, reports, HTML pages, caches) go through
`swgoh_reviewer/io.py:atomic_write_text` (temp file + `os.replace`), so a
reader during a nightly regenerate never sees a partial file. `compose.yaml`
runs the app + comlink (+ Caddy behind a `web` compose profile) with hard
memory limits (`mem_limit` + `memswap_limit`) — see `deploy/DEPLOY.md`.
Self-updates are handled by a host cron running `update-app.sh` (every 10 min:
compares the registry digest of the app image to the local one and pulls +
recreates only when they differ — silent no-op otherwise); the compose project
is pinned via top-level `name: swgoh-reviewer` so the script targets the same
stack regardless of the working directory.

## Verification patterns

- Compile-check: `uv run python -m py_compile *.py swgoh_reviewer/*.py`.
- Unit tests: `uv run pytest` (pipeline: summary pruning, role passthrough,
  compact writers — no comlink or live data needed).
- Report sanity: `uv run python squad_report.py <guild_id> --player <allycode>`.
- If the HTML looks wrong, it's usually the inline JS: the page is a **Jinja2**
  template (`swgoh_reviewer/calc.py` `HTML_TEMPLATE`, rendered via
  `Environment` at the bottom of `main()`) with data inlined as
  `const DATA = {...}`. Verify JS with `node --check` on the second
  `<script>` block, or render in jsdom and inspect each tab (matrix rows =
  players+2, cells = players×squads). Keep JS braces single in the template
  (Jinja2's `{{ ... }}` are the only doubles).

## Gotchas

- The comlink binary is a bundled Node app with a **baked-in V8 heap cap**;
  `NODE_OPTIONS`/`mem_limit` can't raise it, which is why game data comes from
  the static repo instead of comlink's `/data`. Keep comlink to `/player` and
  `/guild` calls only.
- The static unit catalog (`units.json.br`) is the canonical roster set (410
  baseIds); comlink's `/data` also returned ~111 event/raid/journey variants
  (e.g. `THEMANDALORIANBESKARARMOR_JOURNEY_EVENT`, `..._SPEEDERBIKERAID`).
  Those never appear in player rosters, so faction matching is unaffected.
  `names.json` is likewise the 410-unit set (was 521 when built via comlink).
- The job runner's lock must stay **reentrant**: `refresh_guild` calls `regen`
  while holding it, so a plain `threading.Lock` deadlocks the job worker (the
  fetch completes but pages never regenerate). Use `threading.RLock`.
- `rote.py` now builds the TB doc **only from the static gamedata** and errors
  if `SWGOH_GAMEDATA_BASE=""`; the old `data/rote/*.json` comlink raws are
  stale/unused.
- Relic-scale calibration anchor: player `679577173`'s `GLLEIA` has raw
  `relic.currentTier` 11 == R9 — handy for sanity-checking any new relic field.
- Unit matching is by display name (unique per unit in practice); `baseId` is
  recorded in outputs for precision. Some units share a display name across
  baseIds (e.g. "The Mandalorian (Beskar Armor)" has journey-event variants) —
  harmless for matching, cosmetic for missing-unit baseId resolution.
- `relicLevel` in outputs (summaries, TB docs) is the **in-game relic level**,
  `max(0, raw - 2)`, where 0 = no relic and the max is R10. The game data /
  player data carry the raw `relic.currentTier` (and raw requirement fields
  like `minimumRelicTier`, `unitRelicTier`) on an offset scale: 1–2 = no relic,
  3 = R1, ..., 12 = R10 — apply `- 2` wherever raw values are shown (this was
  burned once: raw 11 was displayed as "R11", which does not exist in game).
  Ability tiers are 0-based indexes into the skill def; zeta/omicron flags
  compare the player's tier against the def's `isZetaTier`/`isOmicronTier`
  index.
- Faction `pool` by tag matches characters only (ships excluded).
- `commonRelic` squad results carry `commonRelic`/`nextThreshold`/`bottlenecks`
  instead of `complete`/`gap`; a missing required unit yields `commonRelic:
  null`. Matrix/HTML branch on `mode` accordingly.
