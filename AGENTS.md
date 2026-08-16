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
`rote_platoons.py` (platoon planner page), `rote_assignments.py` (assignments
by member), `start_comlink.sh` (comlink in Docker).

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
  `.squads.html`; `rote_calc.py <guild_id>` → `.calculator.html`;
  `rote_platoons.py <guild_id>` → `.platoons.html`;
  `rote_assignments.py <guild_id>` → `.assignments.html`. The server
  admin's "Regenerate pages" runs all of them.
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
  `node --check` on the second `<script>` block plus `npm test` (jsdom).

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
- `data/guilds/<guildId>.platoons.html` — day-by-day platoon assignment
  planner (`rote_platoons.py`); see "Platoon planner" below.
- `data/guilds/<guildId>.assignments.html` — read-only per-member assignment
  roster (`rote_assignments.py`); a light sibling of the planner
  (`build_data(light=True)` drops unit maps / slot c/gl).

## Tools & conventions

### ROTE star calculator (`rote_calc.py`)

Self-contained Jinja2 HTML page (`HTML_TEMPLATE`) with data inlined as
`const DATA = {...}`; app logic is the second `<script>` block (single JS
IIFE). Inputs: `data/rote/t05D.json` planets (star thresholds, op platoon
rewards, CM max from each mission's `pointsPerWave` × `CM_MULTIPLIER`) plus
`data/guilds/<guildId>.summary.json` guild GP. Plans live in **per-guild**
`localStorage` keys; the calculator also starts from the server's published
guild plan when one exists (and `?planId=` opens a server plan). A "Share"
button copies a URL with the plan payload embedded (`?plan=<base64url>`), and
opening such a URL loads it as an editable "Shared" plan. Verify the JS with
`node --check` on the second `<script>` block and `npm test` (jsdom:
calculator optimizer sanity 47/52/43/41, share round-trip, guild-plan load,
dashboard matrix — data-dependent, re-derive on game updates).
Full model (per-day aggregate `compute()`, UI/state, optimizer):
`docs/rote-calculator.md`.

### Op-fill planning (`rote_ops.py`)

- Every unit listed in a planet's op is a required fill (6 platoons × 15 = 90).
  A member qualifies at `relic >= op relic` (characters) or 7 stars (ships),
  fills each distinct unit at most once, capped at 10 units per planet.
  Greedy: hardest fills first (fewest qualifiers), then lowest qualifying
  relic, then fewest assignments so far, then highest GP. "Closest" = owners
  below the requirement (top 5).
- Requires `rarity` in the guild summary (ships), so re-run `guild_summary.py`
  after a roster refresh.

### Platoon planner (`rote_platoons.py`)

Self-contained Jinja2 page (same shape as the calculator: `const DATA` +
second `<script>` IIFE) served at `/g/<id>/platoons`. Day tabs 1–6; the planets
shown on a day come straight from the star plan (`state.days[d]`, plus any
planet with fills that day) — the calculator decides accessibility, the
planner does no phase math. Planets are listed in the calculator's
dark → neutral → light → specials order (`build_data` tags each planet with an
`order` from its `conflict<N>`/`_bonus` id). Each planet is a row of 6 platoon columns × 15
slot cells, collapsible per planet. Assignments are manual per (planet, day,
slot): each cell has an eligible-count badge and a chip (right-aligned) that
opens a popover listing only members who qualify (relic ≥ req, ships 7★),
sorted by relic, with a "Clear today's fill" row; options already conflicted
that day are dimmed with a tooltip. A slot's latest assignment on or before
the viewed day covers it (chip shows the member), and a covered slot can still
be reassigned on later days without removing the earlier fill. Conflicts
surface as warnings: >10 fills per member per planet per day, a unit placed
twice by one member in a day (across all planets), and completing a platoon
before the planet's planned star day.

Auto-generation (`generateAssignments(scope, strategy, policy)`) is triggered
from contextual `auto` buttons (header "Generate all", each day line, each
planet header) that open one popup with two choices: **platoon filling**
("fill according to plan" — complete the plan's `platoons` count, preload the
rest to 14/15, leaving a Galactic Legend / least-constrained unit unassigned
per platoon; or "fill fully" — complete everything) and **member selection**
(strongest / weakest / minimize assignments per day). Fills only uncovered
slots, seeded from existing fills; a header "Clear all" wipes fills but keeps
the star plan. `setFill` is the shared mutation helper; `genPick` is the pure
strategy picker.

The planner shares the calculator's per-guild plan objects
(`roteCalcPlans:<guildId>` / `roteCalcCurrent:<guildId>`) and adds a `fills`
field keyed `planet → day → slotIdx → allyCode` (slotIdx = platoon*15+pos).
Plans are shared between browsers by **Export/Import JSON** — the file carries
the star plan (`days`) plus the fills, with slots as `"platoon:pos"` and
members as allyCodes; the calculator's `?plan=` URL stays star-plan-only.
`calc.py`'s `persist`/new-plan must merge (preserve `fills`), and `platoons.py`
must never drop `days`. Verify: `node --check` the second `<script>` block and
`npm test` (jsdom: day tabs, coverage + reassign, the three conflicts,
eligible-only picker, export→import round-trip, unknown-member import,
generation modes + GL open-slot + genPick).
`server/jobs.py:regen`
builds it non-fatally next to the calculator.

### Assignments by member (`rote_assignments.py`)

Read-only roster at `/g/<id>/assignments` listing every member's platoon
assignments across all days, from the same plan `fills` (same localStorage
keys). Summary table: member, total fills, per-day counts; each row expands
to the detail (`Day · Planet · Platoon N · unit`), flagging per-(member,
planet, day) groups over the 10-cap, and has a `copy` button that puts a
Markdown version of that member's assignments on the clipboard. Uses
`build_data(light=True)` (no unit maps / slot combat/GL) so the page is ~10×
smaller than the planner; search filter + expand/collapse all. Wired into
`server/jobs.py:regen` non-fatally.

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
`/g/<id>/{report,calc,platoons,assignments}`; `/g/<id>/squads` needs an
officer/leader role (or admin); `/admin*` needs the signed 24h admin cookie
(token entered at
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
- **Edit `calc.py` JS:** `node --check` the second `<script>` block; run
  `npm test` (jsdom: optimizer sanity 47/52/43/41, share round-trip).
- **Change the ops/TB merge:** ops attach by `linkedConflictId`; platoons are
  numbered by position in `swgoh_rote_operations.json`, not by their id suffix.

## Decisions & deferred work

- Guilds are always-on: every registered guild is public and refreshed nightly
  (no enabled toggle). Removing a guild (admin → Remove guild, confirmed)
  deletes its DB row + `data/guilds/<id>.*` files.
- Internal ids are never surfaced to users — e.g. the ROTE campaign id `t05D`
  is a code constant only (no UI shows it).
- Plans are **server-side** (`guild_plans` table): multiple named plans per
  guild, one `is_current` (the "guild plan" everyone sees), with
  `GET/POST /g/<id>/plan(s)` and `PUT /g/<id>/plans/{id}`,
  `POST .../{id}/current`, `DELETE .../{id}`. Writes are gated on the admin
  session today (`canPublish` is derived server-side; when Discord OAuth is
  configured, officer roles will be admitted to the same endpoints). The
  planner/assignments fetch the guild plan; the planner has Save-to-server /
  Publish-to-guild buttons; `?planId=` opens a server plan in the calculator.
  Per-guild browser `localStorage` plans and the JSON export/import + `?plan=`
  payload share still work as personal drafts.
- Page-JS verification runs via `npm install && npm test` (jsdom, dev-only; not
  in the Docker CI). Fixtures are generated from the local `data/` at test time.
