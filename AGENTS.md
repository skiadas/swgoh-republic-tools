# AGENTS.md

Guidance for working in this repository. It downloads and reports on player
data for STAR WARS: Galaxy of Heroes (SWGOH) via a local `swgoh-comlink`
service (live `/player` and `/guild` calls) plus static game-data files from
the `swgoh-utils/gamedata` repo. No swgoh.gg scraping. Everything runs with
`uv run python <script>`; tests with `uv run pytest`.

## Architecture

The site is **server-rendered hypermedia**: FastAPI renders Jinja pages per
request from `templates/` (shared `base.html` with the guild nav), and **htmx**
swaps server-rendered HTML fragments for interactivity (assigned slots, picker
popovers, day tabs, search, generation, the calculator controls). There is no
client-side framework and no build step; the only client JS is a tiny snippet
per page plus htmx itself (`/static/htmx.min.js`, fetched at build via
`scripts/fetch-htmx.sh` / the Dockerfile).

All page logic lives in **Python** and is unit-testable:
`calc_logic.py` (star-calculator compute + optimizer), `planner.py` (conflict
engine + auto-generation), `report_logic.py` (squad-report views),
`assignments_logic.py` (per-member roster + Markdown). The four offline
page-render tools were retired; the **data builders** remain: `rote.py` (TB
doc → `data/rote/t05D.json`), `squad_report.py` (→ `squads.json`),
`guild_summary.py` (→ `summary.json`), `fetch_guild.py` (refresh via comlink),
`build_caches.py`.

## Ground rules

- **Game data comes from the static repo, not comlink.** `StaticGameData`
  (`swgoh_reviewer/static_gamedata.py`) is a duck-typed drop-in for the
  game-data methods of the comlink-python API, served from the
  `swgoh-utils/gamedata` GitHub repo (env `SWGOH_GAMEDATA_BASE`; set to `""`
  to fall back to comlink). Keep comlink to the live `/player` and `/guild`
  calls only.
- **Don't try to raise comlink's memory.** Its heap cap is baked into the
  bundled Node binary; `NODE_OPTIONS`/`mem_limit` can't change it.
- **All artifact writes go through `swgoh_reviewer/io.py:atomic_write_text`**
  (temp file + `os.replace`) so readers never see a partial file.
- **All page logic lives in `swgoh_reviewer/`; `server/app.py` routes render
  Jinja templates and call those modules.** The top-level `*.py` are thin CLI
  wrappers for the data builders.
- **Relic values in any output are the in-game relic level** `max(0, raw - 2)`
  (0 = no relic, max R10). Raw game/player values (`relic.currentTier`,
  `minimumRelicTier`, `unitRelicTier`) sit on an offset scale (1–2 = no relic,
  3 = R1, ..., 12 = R10). Never display a raw tier.
- **The job runner's lock must stay reentrant** (`threading.RLock`):
  `refresh_guild` calls `regen` while holding it; a plain `threading.Lock`
  deadlocks the worker.
- **Jinja templates use autoescape** (`Jinja2Templates` default). Data that
  goes into JS (the `const DATA`-style payloads are gone; fragments are pure
  HTML) must be escaped via Jinja; htmx `hx-vals` attributes are JSON-in-a-
  single-quoted-attribute — keep planet names/apostrophes in mind.
- **Faction `pool` by tag matches characters only** (ships excluded).

## Common operations

**Data builders** (`uv run python <tool>`): `fetch_guild.py` (fetch/refresh a
guild), `guild_summary.py` (offline summary rebuild), `build_caches.py`
(rebuild game caches), `squad_report.py` (squad report data), `rote.py` (TB
doc), `start_comlink.sh` (comlink in Docker).

- **Run / test / compile:**
  ```bash
  uv run python server/app.py        # or: uv run uvicorn server.app:app --port <port>
  uv run pytest                      # unit + route/fragment tests
  uv run pytest -m browser           # Playwright browser tests (needs: playwright install chromium)
  uv run python -m py_compile *.py swgoh_reviewer/*.py server/*.py tests/*.py
  ```
  The browser suite (`tests/test_browser.py`) boots the real app against the
  local `data/` and drives Chromium to verify the htmx pages actually work in a
  browser (the route tests can't run JS). It's the safety net for interactive
  regressions — run it after touching templates or htmx wiring.
- **htmx**: `./scripts/fetch-htmx.sh` (pin `HTMX_VERSION`); the Dockerfile
  does the same at build. Served at `/static/htmx.min.js`.
- **Start comlink (Docker):** `./start_comlink.sh` → `http://localhost:3200`.
- **Fetch / refresh a guild:** `fetch_guild.py <allycode>` (or `--guild-id`)
  streams each member roster through comlink, writes the manifest + summary
  and discards raw rosters. `--limit N` for a small batch.
- **Rebuild game-data caches after a game update:** `fetch_guild.py
  --refresh-game`, `guild_summary.py --refresh-game`, `build_caches.py`, or
  `rote.py --refresh`. The admin "Rebuild game data" button runs the same
  stack (caches + name map + ROTE doc) as a background job, and the nightly
  job rebuilds it before the guild refreshes.
- **Rebuild the squad report data:** `squad_report.py <guild_id>` (also runs
  after every guild refresh and after saving squad definitions — there is no
  manual button). The calculator/planner/assignments pages render live from
  the caches + plans.
- **Run the web app locally (mirrors the deployed stack):**
  ```bash
  docker compose -f compose.yaml -f compose.dev.yaml up -d
  ```
  `compose.dev.yaml` is a dev override of the same `compose.yaml` family the box
  runs: it brings up comlink + the ae2 portrait extractor, builds the repo image,
  bind-mounts `swgoh_reviewer/ server/ templates/ scripts/ data/`, publishes the
  app on `SWGOH_PORT` (default 8500), and runs `uvicorn --reload`. Python edits
  hot-restart automatically; `templates/*.html` are re-read by Jinja on every
  render (no restart). `server/static/htmx.min.js` is committed, so the
  bind-mounted `server/` carries it without a container-start fetch (only the
  image build runs `scripts/fetch-htmx.sh`).
  Provision a guild via the admin hub (register + "Rebuild game data" pumps
  caches, ROTE doc, and unit portraits through ae2 into `data/game/assets/`).
  The minimal fallback `uv run python server/app.py` still works for route tests
  against an existing `data/`, but is *not* the harness the deployed stack is
  validated against.
- **Deploy:** push to `main` → CI builds the image → the box's
  `update-app.sh` cron pulls + recreates the app. See `deploy/DEPLOY.md`.

## Plans (server-side)

- **`guild_plans` table**: multiple named plans per guild, one `is_current`
  (the "guild plan" everyone sees). `GET/POST /g/<id>/plan(s)`,
  `PUT /g/<id>/plans/{id}`, `POST .../{id}/current`, `DELETE .../{id}`.
- **Editing is on a draft**: `guild_drafts` holds the plan being edited
  (calc/planner edits write it); **Publish to guild** copies the draft into a
  named current plan and clears the draft. Members read the current plan
  (assignments page).
- **Admins maintain multiple plans** via the Plans popover on the calculator
  and planner headers (`_plans.html`; `GET /plans/popover`,
  `POST /plans/working|save`, `POST /plans/{id}/ui-set-current|ui-rename|
  ui-delete`). The popover and its POSTs are `HX-Redirect`/`HX-Refresh`
  driven — htmx 2.x dropped the client `hx-refresh` attribute, so page
  reloads after a plan switch/publish must come from those response headers.
- **Working plan** is per admin session via the `plan_work` cookie
  (`server/app.py:PLAN_COOKIE`): pages show `draft or working or current`
  (`working_base`). Switching plans clears the draft (confirm-gated).
  **Publish** updates the working plan in place (falling back to the current
  plan) and marks it current; **Save as new plan** snapshots the working
  content into a new named plan without changing current. The draft row is
  still shared per guild, so simultaneous officers share the edit buffer.
- **Writes are gated on the admin session or a Discord-signed-in officer** of
  the guild (`require_guild_role`, `can_edit`/`canPublish` derived server-side
  from the linked player's roster role; anonymous → 401, signed-in
  non-officer → 403). Global admin routes (`admin/*`, register/refresh/remove,
  create_link, game-data) stay admin-only. The shared header shows a global
  `Sign in with Discord` / username + `Sign out` control when OAuth is
  configured (`auth_state` Jinja global in `base.html`); officers log in via
  OAuth (`identify` scope) and their role comes from the linked player's
  `memberLevel` (3=officer, 4=leader) in the roster manifest.
- Plans carry `{deployPct, unlockZeffo, unlockMandalore, days, fills}`;
  `fills` is `{planet: {day: {slotIdx: allyCode}}}` (slotIdx =
  platoon*15+pos, day/slot keys as strings after JSON).

## Pages & fragments

- **Guild nav** (`server/nav.py`): Home · Report · Calculator · Planner ·
  Assignments, rendered from `templates/base.html` on every guild page.
- **Calculator** (`/g/<id>/calc`): `templates/calc.html` + `_calc_body.html`;
  the whole body is an htmx form — any control `change` posts the full state
  to `POST /calc/set` (updates the draft, recomputes, re-renders the body).
  Model in `calc_logic.py` (`compute`, `optimize` — verified: 100% → 47/52★,
  50% → 43, 30% → 41; re-derive on game updates).
- **Planner** (`/g/<id>/platoons`): `planner.html` + `_platoons_day.html`,
  `_picker.html`, `_gen.html`; fragments `GET /platoons/day|picker|gen`,
  `POST /platoons/assign|generate|clear|publish`. Logic in `planner.py`
  (coverage, conflicts, generation with policy plan/full + strategy
  strongest/weakest/minimize and the GL-left-open heuristic).
- **Assignments** (`/g/<id>/assignments`): `assignments.html` +
  `_assignments_roster.html`; `GET /assignments/roster?search=`,
  `GET /assignments/member/<ac>/markdown`. Logic in `assignments_logic.py`.
- **Report** (`/g/<id>/report`): `report.html` + `_report_{matrix,squads,
  players,needs}.html`; `GET /report/view?view=`. Logic in `report_logic.py`.
- **Admin / index / login**: `admin.html` (the single admin hub: register
  guild, link Discord user, Rebuild game data, guilds table with per-row
  view/refresh/remove, recent jobs), `index.html`, `admin_login.html` — plain
  Jinja over the existing endpoints (no guild nav). There is no `/admin/g/<id>`
  page; guild actions live in the hub's table.

## Data layout (`data/`)

- `data/<allyCode>.json` — full player rosters, kept only by
  `guild_summary.py`; `fetch_guild.py` no longer writes them.
- `data/names.json` — `baseId -> display name`.
- `data/game/` — compact caches: `units.json` (combatType, categories,
  leader, name, factions), `categories.json`, `localization.json`,
  `factions.json`.
- `data/game/static/` — raw `swgoh-utils/gamedata` downloads.
- `data/guilds/<guildId>.json` / `.summary.json` — manifest + roster summary.
- `data/guilds/<guildId>.squads.json` — squad report data (`squad_report.py`).
- `data/rote/<tbId>.json` / `.md` — merged TB doc + dump (`rote.py`).
- `data/rote/<planet>.ops.json` / `.ops.html` — op-fill planner output
  (`rote_ops.py`, an offline CLI tool, still works).

## When you change X, also do Y

- **Add or edit a page/fragment:** it's a Jinja template extending
  `base.html` (or a fragment), wired from `server/app.py` routes; regenerate
  nothing (server-rendered); add a route/fragment test in `tests/test_server.py`.
  Hold new pages to the usability checklist: they must render for every role
  (anonymous/officer/admin), carry navigation back to the site, handle empty
  data with a notice (no 500), and any `<form>`/`hx-post` target must answer
  with a redirect or HTML fragment — never a bare JSON dump. The `crawl_*`
  browser tests (`test_browser.py`) assert all of this automatically.
- **Edit `calc_logic.py` / `planner.py`:** the model ports were verified
  against the old JS (optimizer 47/52/43/41; generation shapes) — keep
  `tests/test_server.py` green (it asserts those).
- **Change the game-data cache format:** update the staleness guard
  (`_units_projection_ok` in `gamecache.py`), then rebuild caches.
- **Add a relic-bearing field:** normalize with `max(0, raw - 2)` and
  sanity-check against the calibration anchor: player `679577173`'s `GLLEIA`
  has raw `relic.currentTier` 11 == R9.
- **Touch the job runner:** keep the lock reentrant (`threading.RLock`).
- **Change the ops/TB merge:** ops attach by `linkedConflictId`; platoons are
  numbered by position in `swgoh_rote_operations.json`, not by their id suffix.
- **Add/change a feature that depends on the real stack (comlink, ae2, game
  data, portraits):** it must be wired in `compose.yaml`/`compose.dev.yaml`
  (the deploy family — not a bespoke local script) and exercised by a
  deterministic test fixture (a seeded asset/cache in a tmp dir or the browser
  suite's `seed_portraits`), not validated against leftover state in the local
  `data/`. "Implemented" means the dev harness can show it, not just that a
  unit test asserts a dict.

## Decisions & deferred work

- Guilds are always-on: every registered guild is public and refreshed nightly.
  Removing a guild (admin → Remove guild, confirmed) deletes its DB row +
  `data/guilds/<id>.*` files.
- Internal ids are never surfaced to users — e.g. the ROTE campaign id `t05D`
  is a code constant only.
- Plans are server-side (see above); local drafts + JSON export/import were
  superseded by the draft/publish model. Discord OAuth (`identify` scope)
  admits officer/leader roles to calc/planner/plan editing via the linked
  player's roster `memberLevel`; a real Discord-server role check or
  self-service linking would be follow-ups.
- The working plan is a per-admin cookie, but the draft edit buffer is still
  guild-wide (one `guild_drafts` row) — concurrent officers share it; per-admin
  drafts would be a follow-up if needed.
- Page-JS verification uses **Playwright** (`uv run pytest -m browser`, needs
  `playwright install chromium`); the pytest route tests can't run JS, so touch
  htmx templates → run the browser suite.
