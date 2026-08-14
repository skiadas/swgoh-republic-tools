# ROTE star calculator — model reference

Deep model of the interactive ROTE day-by-day star calculator page. This is
only needed when editing `swgoh_reviewer/calc.py` (`HTML_TEMPLATE` + the
`build_data` inputs); it is not required for day-to-day work in this repo.

The page is a self-contained Jinja2 HTML page (`HTML_TEMPLATE`) with data
inlined as `const DATA = {...}`; the app logic is the second `<script>` block
(a single JS IIFE). Verify the JS with `node --check` on that block, or render
in jsdom and inspect each tab (matrix rows = players+2, cells = players×squads).

## Inputs (from `build_data`)

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

## Per-day aggregate model (JS `compute()`)

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

## UI / state

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

## Optimizer (in-page, `optimizePlan`)

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
