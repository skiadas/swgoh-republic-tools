# AGENTS.md

Guidance for working in this repository. It downloads and reports on player
data for STAR WARS: Galaxy of Heroes (SWGOH) using a self-hosted
`swgoh-comlink` service (a local gateway to EA's read-only game APIs). No
swgoh.gg scraping.

## Pipeline

All logic lives in the `swgoh_reviewer/` package; the top-level `*.py`
scripts are thin CLI wrappers. Data paths are env-driven (`SWGOH_DATA_ROOT`,
`SWGOH_COMLINK` — see `swgoh_reviewer/config.py`).

```
start_comlink.sh            run swgoh-comlink (Docker) once
fetch_guild.py <allycode>   fetch a guild and write its summary, streaming
guild_summary.py <guild_id> rebuild a summary from existing raw rosters (dev)
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

## Setup / running

- Everything runs with `uv run python <script>` (Python 3.10+; deps in
  `pyproject.toml`, locked by `uv.lock`). Tests: `uv run pytest`.
- The only network dependency is `swgoh-comlink`, started via
  `./start_comlink.sh` (Docker, listens on `http://localhost:3200`). Most
  scripts are offline; only first-time game-data cache builds contact it.
- macOS: the prebuilt comlink *binary* is broken on this machine (pkg/V8
  mismatch) — use the Docker container.

## Data layout (`data/`)

- `data/<allyCode>.json` — full player rosters kept only by `guild_summary.py`
  (offline rebuild); `fetch_guild.py` no longer writes these.
- `data/names.json` — `baseId -> display name` cache for all units.
- `data/game/` — compact game-data caches built from comlink:
  `units.json`, `categories.json`, `localization.json` (skills.json was dropped —
  nothing reads ability/zeta/omicron data anymore).
- `data/guilds/<guildId>.json` — guild manifest (member list, GP, statuses,
  `memberLevel` roles).
- `data/guilds/<guildId>.summary.json` — compact per-member roster summary
  (units: name, baseId, combatType, gearLevel, relicLevel, rarity, factions,
  leader). Written compactly (~3.6MB vs the old ~25MB).
- `data/guilds/<guildId>.squads.json` — squad report (`bySquad` + `byPlayer`).
- `data/guilds/<guildId>.squads.html` — generated dashboard.
- `data/swgoh-gg-ops*.html` — manually saved swgoh.gg "Territory Battle Platoons"
  pages, one per phase (the phase is detected from the planet list inside).
- `data/rote/<tbId>.json` — merged TB doc (phases -> planets -> missions with
  deploy requirements + ops with platoons/units) built by `rote.py`. Each planet
  carries `starThresholds` (the 3 galactic-score star cutoffs) and each mission
  carries `waves` + `pointsPerWave` (per-wave galactic-score deltas from the
  `table` collection).
- `data/rote/<tbId>.md` — human-readable TB dump.
- `data/rote/<planet>.ops.json` / `.ops.html` — op-fill planner output for one
  planet (`rote_ops.py`): which fills are covered, missing, closest owners,
  member assignments.
- `data/guilds/<guildId>.calculator.html` — interactive day-by-day star
  calculator for that guild (`rote_calc.py`), a self-contained HTML page.
  Full model below.
- `data/rote/*.json` — raw comlink collections cached for offline re-runs, but
  only the target TB's slice of `campaign` (91MB -> ~0.7MB) and
  `territoryBattleDefinition` are kept.

## ROTE star calculator (`rote_calc.py`)

Self-contained Jinja2 HTML page (`HTML_TEMPLATE`) with data inlined as
`const DATA = {...}`; app logic is the second `<script>` block (single JS
IIFE, `node --check` the block and jsdom-verify tabs like `render_report.py`).

### Inputs (from `build_data`)

- `data/rote/t05D.json` planets: `starThresholds` (3 cutoffs), platoon
  `reward` (first platoon of each op) + `platoonsTotal`, CM max =
  sum of each mission's `pointsPerWave` × `CM_MULTIPLIER` (50). Each planet
  also carries `phase` and `relicReq` (op relic requirement) for the
  optimizer's phase-level estimates.
- `data/guilds/<guildId>.summary.json` → `guildGp` (sum of member GP).
- Planets keyed by `conflict<N>` suffix in `planetId` (`_bonus` → Zeffo
  `conflict1_bonus` / Mandalore `conflict3_bonus`): `conflict1`=Light,
  `conflict2`=Dark, `conflict3`=Neutral. `PLANET_ORDER` = dark, neutral,
  light, zeffo, mandalore.

### Per-day aggregate model (JS `compute()`)

For each of the 6 days, only planets "accessible" this day are modeled:
chain planet `n` (n = planets finished so far) plus any unlocked special
planets. Each planet is either **finish** (goal 1–3, `minTP`/`maxTP` +=
`thresholds[stars-1]`) or **preload** (goal 0, `maxTP` +=
`thresholds[0] - 1`; preloading earns partial points and deliberately
cannot star the planet). Then:

- `platoon` = Σ `plats × platoonReward` (plats capped at remaining
  platoons, max 6); `gp` = `deployPct% × guildGp`; `estCM` = Σ `cmPct% ×
  cmMax`; `totalCM` = Σ accessible `cmMax`.
- `cmMinPts = minTP − bankIn − platoon − gp`, `cmMaxPts = maxTP − bankIn −
  platoon − gp`; reported as `minPct`/`maxPct` = these ÷ `totalCM`, clamped
  0–100. `feasible` = `cmMinPts ≤ totalCM` (unclamped — reachable with 100%
  CM); `shortEst` = feasible but `cmMinPts > estCM` (the set CM% sliders fall
  short). Day sections get a red "Cannot reach" banner when infeasible and an
  amber "Estimated CM falls short" banner otherwise (`.day.bad`/`.day.low`,
  `#banner-<day>`); the min-CM metric shows the true value in red when
  infeasible.
- `capacity` = `maxTP − minTP` (points the preloads can bank);
  `carry` = clamp(`bankIn + gp + platoon + estCM − minTP`, 0, `capacity`)
  becomes next day's `bankIn`. `wastedGp` = clamp(`bankIn + gp + platoon +
  estCM − maxTP`, 0, `gp`) — the deploy GP that can't be applied because it
  would star a preloaded planet (shown as "wasted deploy" in the day panel).
- Stars: finish credits `stars` (special planets credit only 1 if `stars
  === 3`), advances the chain index, and marks the next unlock-toggle once
  the special planet's trigger (`triggerPlanetName` = `chain.planets[
  triggerIndex − 1]`) is finished. `totalStars` = sum across light/dark/
  neutral/zeffo/mandalore.

### UI / state

- Controls per planet column (`.pcol`, 3 rows, 185px): goal seg bar, platoon
  seg bar (0–6, options beyond `remaining` disabled), CM% slider. Goal bars:
  normal planets show ★×goal; **special planets show 🎁×goal for 1–2 and a
  single ★ for 3** (`&#127873;` gift, `&#9733;` star), since only level 3
  stars a special planet. A single `.labels-col` (Star goal / Platoons / CM%)
  sits left of each day's planet grid instead of per-planet labels. Star-goal
  options carry tooltips with that planet's threshold points (`1★: 116,406,250`).
- Overall panel: min CM / max CM / bank in / carry out / wasted deploy; bottom
  line shows minTP·maxTP·platoons·deploy·CM avail plus a ⚠ warn when
  infeasible.
- State = `deployPct` + `unlockZeffo`/`unlockMandalore` + `days[d][planet]`
  `{goal, platoons, cmPct}`, persisted per plan in `localStorage`
  (`roteCalcPlans` key, current name in `roteCalcCurrent`). The current plan is
  auto-saved on every change; the header "New Plan" popup (`openNewPlan`/
  `createNewPlan`) creates an additional plan, either duplicating the current
  one or starting blank, and switches to it. `setGoal`/`setPlatoon` re-render;
  `cmInput`/`deployInput` only patch the results panel via `updateResults()`.
  Handlers keyed by `d<day>-<planet>[-plats]` ids/radio names; the `-cm`
  slider id also drives `cmInput`.
- Numbers: `fmt()` shows scores compactly by default (`1.2B` / `234.3M`, at
  most one decimal) while `pfmt()` keeps exact values for tooltips; the
  header "compact numbers" checkbox toggles `fmt()` between compact and
  `toLocaleString()` (persisted in `roteCalcCompact`, default on).

### Optimizer (in-page, `optimizePlan`)

Popup from the header "Optimize" button; additive — manual mode is unchanged.
CM estimates can be given two ways (toggle, persisted in `roteCalcOptMode`):
**by level** — one slider per phase (relic tier), every planet in a phase
shares its estimate; or **by planet** — each planet its own slider, grouped on
that level's line in the standard dark/neutral/light/specials order (Zeffo
counts in level 3, Mandalore in level 4). A **P0–P6 platoon expectation per
phase** sets how many platoons you expect to fill on each planet when it is
starred (default 6); plus "Unlock & max" checkboxes for Zeffo / Mandalore,
deploy %, guild GP. Estimates and platoon expectations persist in
`localStorage` (`roteCalcOptEst`, `roteCalcOptPlanet`, `roteCalcOptPlats`);
until saved they seed from per-level defaults `{1:30, 2:20, 3:10, 4:5, 5:0,
6:0}` (a fresh plan), and "Reset estimates to defaults" restores those. JS
beam search (width 90, dedup by chain position keeping max stars/bank) over
the 6 days; the best plan is applied to the calculator's own state via the
modal handlers.

Model: per day a goal combo (0 = preload, 1–3 = finish stars; specials only
0 or 3) over the accessible planets is accepted only when
`bankIn + deploy + platoon + estCM >= minTP` (hard limit — stricter than
`compute()`'s `feasible`, so applied plans never show the ⚠ warn). Platoons
are filled on the day a planet is starred only, up to the phase's platoon
expectation (`platOf()` caps per phase; missing phase → 6). A checked special
must finish at 3★ (credits 1) by day 6; if unreachable the result warns and
returns best-effort. Carry/bank identical to `compute()`.
`window.optimizePlan(est, unlockZ, unlockM, deploy, platCap)` — `est` keyed by
planet name — returns `{stars, cs, days, unmet}`. `estOf()` resolves each
planet's estimate: `0` is honored as-is, only a missing key falls back to 100.
The popup's per-day plan lines list planets in the main view's
dark/neutral/light/specials order. Sanity reference (jsdom-verified): 100% CM
→ 47 stars no unlocks / 54 both specials; 50% → 43; 30% → 41.

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

- Merges two sources: (1) saved `data/swgoh-gg-ops*.html` pages for the
  op/platoon unit lists (baseIds + relic requirement + rewards), and (2)
  comlink `territoryBattleDefinition` + `campaign` for the structure and
  combat-mission deploy requirements (`entryCategoryAllowed`: allowed
  factions, mandatory units, relic/rarity/mod thresholds), enemies, rewards.
- swgoh.gg phases load via JS tabs, so each phase must be saved separately by
  hand; swgoh.gg is 403-blocked to scripts. `--refresh` re-fetches comlink
  raws; the op pages are always re-read from disk.

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
  rebuild `data/game/` caches after a game update. The report scripts only
  need comlink for this; otherwise fully offline.

## Web service (`server/`)

FastAPI app serving each registered guild's generated pages, with a
token-gated admin. Run locally with `uv run uvicorn server.app:app` (or
`uv run python server/app.py`). Env config:

- `SWGOH_DATA_ROOT` — data directory (shared with the CLI tools).
- `SWGOH_COMLINK` — swgoh-comlink URL (fetch/refresh only).
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
lock), `server/auth.py` (Discord OAuth + signed cookies + roster-derived
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
Self-updates are handled by a host cron (`docker compose pull app && up -d
app` every 10 min); the compose project is pinned via top-level
`name: swgoh-reviewer` so the cron targets the same stack regardless of the
working directory.

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

- Unit matching is by display name (unique per unit in practice); `baseId` is
  recorded in outputs for precision. Some units share a display name across
  baseIds (e.g. "The Mandalorian (Beskar Armor)" has journey-event variants) —
  harmless for matching, cosmetic for missing-unit baseId resolution.
- `relicLevel` is the raw relic `currentTier` (0 = no relic). Ability tiers are
  0-based indexes into the skill def; zeta/omicron flags compare the player's
  tier against the def's `isZetaTier`/`isOmicronTier` index.
- Faction `pool` by tag matches characters only (ships excluded).
- `commonRelic` squad results carry `commonRelic`/`nextThreshold`/`bottlenecks`
  instead of `complete`/`gap`; a missing required unit yields `commonRelic:
  null`. Matrix/HTML branch on `mode` accordingly.
