#!/usr/bin/env python3
"""ROTE platoon assignment planner — interactive self-contained HTML page.

For a guild, a day-by-day planner over each planet's platoon slots (6 platoons
x 15 units). Assignments are recorded manually; the page surfaces conflicts:

  - a member can fill at most 10 slots on a planet on a day,
  - a member can place a given unit only once per day (across all planets),
  - completing a platoon (all 15 slots filled) before the planet's planned
    star day is warned about,
  - assignees below the op relic / ship-star requirement are flagged.

Fills persist: a slot assigned on an earlier day is shown as covered (who and
when) but can still be reassigned on later days; the earlier fill is kept.

The page shares the same per-guild plan objects as the ROTE calculator
(localStorage `roteCalcPlans:<guildId>` / `roteCalcCurrent:<guildId>`), adding
a `fills` field to the star plan. A plan is shared between browsers by
exporting/importing a JSON file that carries both the star plan (`days`) and
the assignments (`fills`); the calculator's `?plan=` URL stays star-plan-only.

Model (per AGENTS.md + user rules):
  - Slots are numbered by position: slot = platoon_index * 15 + offset.
  - A member qualifies for a slot at relic >= op relic requirement
    (characters) or rarity >= 7 stars (ships).
  - A slot's latest assignment on or before the viewed day covers it.

Usage:
    python rote_platoons.py
    python rote_platoons.py NW4t0-dBRcG8n-PVhykpKg
"""

import argparse
import json
import re
import sys
from pathlib import Path

from jinja2 import Environment

from swgoh_reviewer.config import data_root
from swgoh_reviewer.io import atomic_write_text
from swgoh_reviewer.ops import load_combat_types

DEFAULT_GUILD = "NW4t0-dBRcG8n-PVhykpKg"
TB_ID = "t05D"
SHIP_STAR_REQ = 7
MAX_UNITS_PER_MEMBER = 10
SLOTS_PER_PLATOON = 15

# Display order matching the calculator's dark/neutral/light/specials grouping.
PLANET_ORDER = {"dark": 0, "neutral": 1, "light": 2, "zeffo": 3, "mandalore": 4}


def _planet_order(planet_id):
    m = re.search(r"conflict(\d+)(_bonus)?", planet_id)
    if not m:
        return 9
    idx, bonus = int(m.group(1)), bool(m.group(2))
    if bonus:
        if idx == 1:
            return PLANET_ORDER["zeffo"]
        if idx == 3:
            return PLANET_ORDER["mandalore"]
        return 9
    if idx == 1:
        return PLANET_ORDER["light"]
    if idx == 2:
        return PLANET_ORDER["dark"]
    if idx == 3:
        return PLANET_ORDER["neutral"]
    return 9


def load_gl_units(outdir):
    """baseIds tagged 'galactic_legend' in the game unit catalog."""
    p = outdir / "game" / "units.json"
    if not p.exists():
        return set()
    out = set()
    for base, meta in json.loads(p.read_text()).items():
        if "galactic_legend" in (meta.get("categories") or []):
            out.add(base)
    return out


def build_data(outdir, guild_id, tb_id=TB_ID):
    rote = json.loads((outdir / "rote" / f"{tb_id}.json").read_text())
    summary = json.loads((outdir / "guilds" / f"{guild_id}.summary.json").read_text())
    unit_combat = load_combat_types(outdir)
    gl_units = load_gl_units(outdir)

    planets = []
    for ph in rote.get("phases", []):
        for p in ph.get("planets", []):
            op = p.get("op") or {}
            platoons = []
            for pl in op.get("platoons") or []:
                slots = []
                for u in pl.get("units") or []:
                    ct = unit_combat.get(u["baseId"])
                    slots.append({"b": u["baseId"], "n": u["name"], "c": 2 if ct == 2 else 1, "gl": 1 if u["baseId"] in gl_units else 0})
                platoons.append({"idx": pl.get("platoon", len(platoons) + 1), "slots": slots})
            planets.append(
                {
                    "name": p["name"],
                    "phase": ph.get("phase"),
                    "relicReq": op.get("relicRequirement") or 0,
                    "platoons": platoons,
                    # Display order matches the calculator's
                    # dark/neutral/light/specials grouping (conflict1=light,
                    # conflict2=dark, conflict3=neutral; _bonus => special).
                    "order": _planet_order(p.get("planetId") or ""),
                }
            )

    # Ship-ness of any slot not in the unit catalog falls back to the guild
    # roster (summary units carry combatType).
    ship_units = set()
    for m in summary.get("members", []):
        for u in m.get("units") or []:
            if u.get("combatType") == "ship":
                ship_units.add(u["baseId"])
    for planet in planets:
        for pl in planet["platoons"]:
            for s in pl["slots"]:
                if s["c"] == 1 and s["b"] in ship_units:
                    s["c"] = 2

    members = []
    for m in summary.get("members", []):
        units = {}
        for u in m.get("units") or []:
            units[u["baseId"]] = [
                u.get("relicLevel") or 0,
                u.get("rarity") or 0,
                1 if u.get("combatType") == "ship" else 0,
            ]
        members.append({"ac": m.get("allyCode"), "name": m.get("name"), "u": units})

    return {
        "guildId": guild_id,
        "guildName": summary.get("guildName", guild_id),
        "planets": planets,
        "members": members,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Platoon planner — {{ guild_name }}</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; background: #fafafa; color: #222; }
  header { background: #1c2541; color: #fff; padding: 12px 20px; }
  header h1 { margin: 0; font-size: 18px; }
  header .sub { font-size: 12px; opacity: .9; margin-top: 4px; }
  header button, header select { font-size: 12px; }
  main { padding: 16px 20px; max-width: 1200px; margin: 0 auto; }
  .tabs { display: flex; gap: 6px; margin-bottom: 14px; }
  .tab { padding: 6px 14px; border: 1px solid #c9d2e3; border-radius: 6px; background: #fff; cursor: pointer; font-weight: 700; }
  .tab.on { background: #1c2541; color: #fff; border-color: #1c2541; }
  .planet { background: #fff; border: 1px solid #ddd; border-radius: 8px; margin-bottom: 12px; }
  .planet > summary { cursor: pointer; padding: 8px 14px; list-style: none; display: flex; flex-wrap: wrap; gap: 10px; align-items: baseline; }
  .planet > summary::-webkit-details-marker { display: none; }
  .pname { font-weight: 700; font-size: 14px; }
  .meta { font-size: 12px; color: #555; }
  .meta b { color: #1c2541; }
  .planet-body { display: flex; gap: 8px; padding: 0 10px 12px; overflow-x: auto; }
  .platoon { flex: 1 1 0; min-width: 148px; border: 1px solid #e2e6ee; border-radius: 6px; background: #f7f9fc; }
  .platoon h4 { margin: 0; padding: 5px 8px; font-size: 12px; background: #e9edf5; border-bottom: 1px solid #e2e6ee; }
  .platoon h4 b { color: #1c2541; }
  .platoon h4 .maxed { color: #2e7d32; }
  .platoon h4 .early { color: #b71c1c; }
  .cell { border-bottom: 1px solid #eef1f6; padding: 4px 6px; font-size: 12px; }
  .cell:last-child { border-bottom: none; }
  .cell .u { font-size: 11px; color: #333; margin-bottom: 2px; line-height: 1.25; }
  .cell select { width: 100%; font-size: 11px; }
  .cell .cov { font-size: 10px; color: #2e7d32; margin-top: 2px; }
  .cell.open { background: #fff; }
  .cell.cov { background: #eef8ee; }
  .cell.cur { background: #e7f0fb; }
  .cell.warn { box-shadow: inset 3px 0 0 #f9a825; }
  .warnbox { border: 1px solid #f9a825; background: #fff8e1; border-radius: 8px; padding: 8px 12px; margin: 10px 0; font-size: 12px; }
  .warnbox ul { margin: 6px 0 0; padding-left: 18px; }
  .legend { font-size: 11px; color: #666; margin-bottom: 12px; }
  .legend span { margin-right: 14px; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
  .muted { color: #888; }
  .notice { border: 1px solid #bdbdbd; background: #eceff1; border-radius: 6px; padding: 6px 10px; margin: 8px 0; font-size: 12px; }
  .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: none; align-items: center; justify-content: center; z-index: 20; }
  .modal-overlay.show { display: flex; }
  .modal { background: #fff; border-radius: 10px; max-width: 420px; width: 90%; }
  .mhead { display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; border-bottom: 1px solid #e5e7eb; }
  .mhead h2 { margin: 0; font-size: 16px; }
  .mbody { padding: 12px 14px; }
  .mfoot { display: flex; justify-content: flex-end; gap: 8px; padding: 10px 14px; border-top: 1px solid #e5e7eb; }
  .cell .u .n { display: inline-block; margin-left: 4px; padding: 0 5px; border-radius: 8px; background: #e9edf5; color: #4a5568; font-size: 10px; line-height: 14px; }
  .cell .chip { width: 100%; display: flex; align-items: center; gap: 4px; border: 1px solid #c9d2e3; border-radius: 5px; background: #fff; color: #333; font-size: 11px; padding: 2px 5px; cursor: pointer; }
  .cell .chip.cov { background: #eef8ee; border-color: #b7d8b7; }
  .cell .chip.cur { background: #e7f0fb; border-color: #b3c9ea; }
  .cell .chip.warn { border-color: #f9a825; box-shadow: inset 0 0 0 1px #f9a825; }
  .cell .chip .lbl { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: left; }
  .cell .chip .arr { opacity: .55; flex: 0 0 auto; }
  .pop { position: fixed; z-index: 60; background: #fff; border: 1px solid #c9d2e3; border-radius: 8px; box-shadow: 0 6px 18px rgba(0,0,0,.18); min-width: 170px; max-width: 240px; max-height: 55vh; overflow: auto; }
  .pop .pop-head { padding: 6px 10px; font-size: 12px; font-weight: 700; border-bottom: 1px solid #e5e7eb; }
  .pop .pop-head .n { color: #667; font-weight: 400; }
  .pop .pick-row { display: flex; align-items: baseline; gap: 6px; padding: 4px 10px; cursor: pointer; font-size: 12px; }
  .pop .pick-row:hover { background: #eef3fb; }
  .pop .pick-row.sel { background: #e7f0fb; font-weight: 700; }
  .pop .pick-row.dim { opacity: .5; }
  .pop .pick-row .lvl { margin-left: auto; color: #555; font-size: 11px; }
  .pop .pick-row.clear { color: #b71c1c; border-top: 1px solid #eef1f6; }
</style>
</head>
<body>
<header>
  <h1>ROTE platoon planner — {{ guild_name }}</h1>
  <div class="sub">
    plan <select id="plan-select" onchange="selectPlan()"></select>
    <button onclick="openNewPlan()">New Plan</button>
    <button onclick="exportPlan()">Export JSON</button>
    <button onclick="document.getElementById('import-file').click()">Import JSON</button>
    <button onclick="openGen({mode:'all'})" title="Auto-generate assignments for all days and planets">Generate all</button>
    <button onclick="clearAll()" title="Clear all platoon assignments">Clear all</button>
    <input type="file" id="import-file" accept="application/json,.json" style="display:none" onchange="importPlanFile(this)">
    &nbsp;<a href="./calc" style="color:#9db3e0">open calculator</a>
    &nbsp;day <b id="day-note">1</b>
  </div>
</header>
<main>
  <div class="tabs" id="tabs"></div>
  <div class="legend">
    <span><span class="dot" style="background:#fff;border:1px solid #ddd"></span>open</span>
    <span><span class="dot" style="background:#eef8ee"></span>covered earlier</span>
    <span><span class="dot" style="background:#e7f0fb"></span>assigned today</span>
    <span><span class="dot" style="background:#fff8e1;border:1px solid #f9a825"></span>conflict</span>
  </div>
  <div id="notice"></div>
  <div id="days"></div>
  <div id="warnings"></div>
</main>
<div class="modal-overlay" id="np-overlay">
  <div class="modal">
    <header class="mhead"><h2>New plan</h2><button onclick="closeNewPlan()">&#215;</button></header>
    <div class="mbody">
      <div>
        <label>Name: <input id="np-name" size="24" placeholder="plan name"
          onkeydown="if(event.key==='Enter')createNewPlan()"></label>
      </div>
      <div style="margin-top:8px">
        <label><input type="radio" name="np-type" value="dup" checked> Duplicate current plan</label>
      </div>
      <div>
        <label><input type="radio" name="np-type" value="blank"> Blank new plan</label>
      </div>
      <div class="muted" style="font-size:12px;margin-top:6px">
        Plans (star goals + platoon assignments) live in this browser and are shared
        by exporting/importing a JSON file.
      </div>
    </div>
    <div class="mfoot">
      <button onclick="closeNewPlan()">Cancel</button>
      <button onclick="createNewPlan()">Create</button>
    </div>
  </div>
</div>
<div class="modal-overlay" id="gen-overlay">
  <div class="modal">
    <header class="mhead"><h2>Auto-generate assignments</h2><button onclick="closeGen()">&#215;</button></header>
    <div class="mbody">
      <div class="muted" id="gen-scope"></div>
      <div style="margin-top:10px"><b>Platoon filling</b></div>
      <div>
        <label><input type="radio" name="gen-policy" value="plan" checked> Fill according to plan (complete the plan's platoon count, preload the rest to 14/15)</label>
      </div>
      <div>
        <label><input type="radio" name="gen-policy" value="full"> Fill platoons fully (complete everything; remove units manually if you don't want a platoon maxed)</label>
      </div>
      <div style="margin-top:10px"><b>Member selection</b></div>
      <div><label><input type="radio" name="gen-strategy" value="strongest" checked> Strongest available unit first</label></div>
      <div><label><input type="radio" name="gen-strategy" value="weakest"> Weakest available unit first</label></div>
      <div><label><input type="radio" name="gen-strategy" value="minimize"> Minimize assignments per player</label></div>
      <div class="muted" style="font-size:12px;margin-top:8px">
        Fills only uncovered slots and respects the 10/planet/day cap and one unit per member per day.
        "Fill according to plan" leaves a Galactic Legend (or least-constrained) unit unassigned in each preloaded platoon.
      </div>
    </div>
    <div class="mfoot">
      <button onclick="closeGen()">Cancel</button>
      <button onclick="generateNow()">Generate</button>
    </div>
  </div>
</div>
<div class="pop" id="picker" style="display:none"></div>
<script>
const DATA = {{ data_json }};
</script>
<script>
(function () {
  "use strict";
  const data = DATA;
  const guildId = (location.pathname.match(new RegExp("^/g/([^/]+)/")) || [])[1] || "unknown";
  const LS_KEY = "roteCalcPlans:" + guildId;
  const LS_CURRENT = "roteCalcCurrent:" + guildId;
  const SLOTS = {{ SLOTS_PER_PLATOON }};
  const MAX_UNITS = {{ MAX_UNITS_PER_MEMBER }};
  const SHIP_STAR = {{ SHIP_STAR_REQ }};
  const PLANET_MAP = {};
  const MEMBER_MAP = {};
  for (const p of data.planets) PLANET_MAP[p.name] = p;
  for (const m of data.members) MEMBER_MAP[String(m.ac)] = m;
  const state = { currentDay: 1, deployPct: 100, unlockZeffo: false, unlockMandalore: false, days: {}, fills: {} };

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function esc(s) { return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
  function memberName(ac) { const m = MEMBER_MAP[String(ac)]; return m ? m.name : String(ac); }
  function slotPlatoon(slot) { return Math.floor(slot / SLOTS); }
  function slotPos(slot) { return slot % SLOTS; }

  // ---- plan persistence (shared with the calculator) ----
  function planName() { return localStorage.getItem(LS_CURRENT) || "Default"; }
  function loadPlans() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {}; } catch (e) { return {}; } }
  function persist() {
    try {
      const plans = loadPlans();
      const prev = plans[planName()] || {};
      plans[planName()] = Object.assign({}, prev, {
        deployPct: state.deployPct, unlockZeffo: state.unlockZeffo,
        unlockMandalore: state.unlockMandalore, days: state.days, fills: state.fills,
      });
      localStorage.setItem(LS_KEY, JSON.stringify(plans));
    } catch (e) { /* storage unavailable */ }
  }
  function loadPlan(name) {
    const saved = loadPlans()[name] || {};
    state.deployPct = saved.deployPct !== undefined ? saved.deployPct : 100;
    state.unlockZeffo = !!saved.unlockZeffo;
    state.unlockMandalore = !!saved.unlockMandalore;
    state.days = saved.days || {};
    state.fills = saved.fills || {};
  }
  function planControlsHtml() {
    const plans = loadPlans();
    const names = Object.keys(plans).length ? Object.keys(plans) : ["Default"];
    let html = '<select id="plan-select" onchange="selectPlan()">';
    for (const n of names) {
      html += '<option value="' + esc(n) + '"' + (n === planName() ? " selected" : "") + ">" + esc(n) + "</option>";
    }
    return html + "</select>";
  }
  window.selectPlan = function () {
    const sel = document.getElementById("plan-select");
    if (!sel) return;
    try { localStorage.setItem(LS_CURRENT, sel.value); } catch (e) { /* ignore */ }
    loadPlan(sel.value);
    render();
  };
  window.openNewPlan = function () {
    document.getElementById("np-name").value = "";
    document.getElementById("np-name").placeholder = "e.g. " + planName() + " (copy)";
    document.querySelector('input[name="np-type"][value="dup"]').checked = true;
    document.getElementById("np-overlay").classList.add("show");
    document.getElementById("np-name").focus();
  };
  window.closeNewPlan = function () { document.getElementById("np-overlay").classList.remove("show"); };
  window.createNewPlan = function () {
    const name = document.getElementById("np-name").value.trim();
    if (!name) { document.getElementById("np-name").focus(); return; }
    const dup = document.querySelector('input[name="np-type"]:checked').value === "dup";
    const prev = dup ? (loadPlans()[planName()] || {}) : {};
    const plan = {
      deployPct: dup ? state.deployPct : 100,
      unlockZeffo: dup ? state.unlockZeffo : false,
      unlockMandalore: dup ? state.unlockMandalore : false,
      days: JSON.parse(JSON.stringify(state.days)),
      fills: dup && prev.fills ? JSON.parse(JSON.stringify(prev.fills)) : {},
    };
    if (!dup) plan.days = {};
    const plans = loadPlans();
    plans[name] = plan;
    localStorage.setItem(LS_KEY, JSON.stringify(plans));
    localStorage.setItem(LS_CURRENT, name);
    state.deployPct = plan.deployPct;
    state.unlockZeffo = plan.unlockZeffo;
    state.unlockMandalore = plan.unlockMandalore;
    state.days = plan.days;
    state.fills = plan.fills;
    closeNewPlan();
    render();
  };

  // ---- plan targets from the calculator (star days) ----
  function planEntry(pn, d) {
    const day = state.days[d];
    return day ? day[pn] : undefined;
  }
  function starDay(pn) {
    for (let d = 1; d <= 6; d++) {
      const g = planEntry(pn, d);
      if (g && Number(g.goal) > 0) return d;
    }
    return null;
  }

  // ---- per-day assignment state ----
  function dayFills(pn, d) {
    const byDay = state.fills[pn] || {};
    return byDay[d] || {};
  }
  function latestAssignee(pn, slot, d) {
    const byDay = state.fills[pn] || {};
    let last = null, lastDay = -1, prev = null, prevDay = -1;
    for (const dd in byDay) {
      const n = Number(dd);
      if (n > d) continue;
      const ac = byDay[dd][slot];
      if (ac === undefined) continue;
      if (n > lastDay) { if (last !== null) { prev = last; prevDay = lastDay; } last = ac; lastDay = n; }
    }
    return { ac: last, day: lastDay, prevAc: prev, prevDay: prevDay };
  }
  function platoonFillCount(pn, platoonIdx, d) {
    const byDay = state.fills[pn] || {};
    let filled = 0;
    for (let s = platoonIdx * SLOTS; s < (platoonIdx + 1) * SLOTS; s++) {
      let last = -1;
      for (const dd in byDay) {
        const n = Number(dd);
        if (n <= d && byDay[dd][s] !== undefined) last = Math.max(last, n);
      }
      if (last >= 0) filled++;
    }
    return filled;
  }
  function wouldCompletePlatoon(pn, slot, d) {
    return platoonFillCount(pn, slotPlatoon(slot), d) >= SLOTS;
  }
  function unitAt(pn, slot) {
    const pl = PLANET_MAP[pn].platoons[slotPlatoon(slot)];
    return pl.slots[slotPos(slot)];
  }

  // ---- conflicts ----
  function countOnPlanetDay(pn, d, ac) {
    let n = 0;
    const byDay = dayFills(pn, d);
    for (const k in byDay) if (byDay[k] === ac) n++;
    return n;
  }
  function unitAssignedOnDay(ac, unitBaseId, d, skipP, skipSlot) {
    for (const pn in state.fills) {
      const byDay = state.fills[pn][d];
      if (!byDay) continue;
      for (const k in byDay) {
        const s = Number(k);
        if (byDay[k] === ac && !(pn === skipP && s === skipSlot)) {
          if (unitAt(pn, s).b === unitBaseId) return true;
        }
      }
    }
    return false;
  }
  function cellWarnings(pn, slot, d, ac) {
    const sl = unitAt(pn, slot);
    const out = [];
    const who = esc(memberName(ac));
    const un = esc(sl.n);
    const cnt = countOnPlanetDay(pn, d, ac);
    if (cnt > MAX_UNITS) out.push({ t: "cap", msg: who + " has " + cnt + " fills on " + pn + " day " + d + " (max " + MAX_UNITS + ")" });
    if (unitAssignedOnDay(ac, sl.b, d, pn, slot)) out.push({ t: "dup", msg: who + " already places " + un + " elsewhere on day " + d });
    if (wouldCompletePlatoon(pn, slot, d)) {
      const sd = starDay(pn);
      if (sd !== null && sd > d) out.push({ t: "early", msg: "completes platoon P" + (slotPlatoon(slot) + 1) + " on day " + d + " but " + pn + " is planned to star day " + sd });
    }
    return out;
  }

  // ---- rendering ----
  // Which planets are worked on a day comes from the star plan (the
  // calculator already figures out accessibility), plus any planet that has
  // fills entered for that day. No phase math here.
  function activePlanets(d) {
    const names = new Set();
    for (const pn in (state.days[d] || {})) names.add(pn);
    for (const pn in state.fills) {
      if (state.fills[pn][d] && Object.keys(state.fills[pn][d]).length) names.add(pn);
    }
    // dark -> neutral -> light -> specials, then phase/name as a stable tiebreak
    return data.planets
      .filter(p => names.has(p.name))
      .sort((a, b) => (a.order - b.order) || (a.phase - b.phase) || a.name.localeCompare(b.name));
  }
  function assigneeOptions(pn, slot) {
    const planet = PLANET_MAP[pn];
    const sl = unitAt(pn, slot);
    const ship = sl.c === 2;
    const req = planet.relicReq;
    const opts = [];
    for (const m of data.members) {
      const u = m.u[sl.b];
      if (!u) continue;
      if (ship ? u[1] < SHIP_STAR : u[0] < req) continue;
      opts.push({ ac: String(m.ac), name: m.name, level: ship ? u[1] : u[0], ship: ship });
    }
    opts.sort((a, b) => (b.level - a.level) || a.name.localeCompare(b.name));
    return opts;
  }
  function cellHtml(pn, slot, d) {
    const sl = unitAt(pn, slot);
    const last = latestAssignee(pn, slot, d);
    const cur = dayFills(pn, d)[slot];
    const warn = cur ? cellWarnings(pn, slot, d, cur) : [];
    const stateCls = cur ? "cur" : (last.ac ? "cov" : "open");
    const opts = assigneeOptions(pn, slot);
    const chipCls = "chip" + (stateCls === "cur" ? " cur" : stateCls === "cov" ? " cov" : "") + (warn.length ? " warn" : "");
    const label = last.ac ? esc(memberName(last.ac)) : "\u2014";
    const tip = last.ac
      ? (last.day === d ? "today" : "since day " + last.day)
      : (opts.length + " eligible");
    const cellTitle = warn.length ? esc(warn.map(w => w.msg).join("; ")) : "";
    return '<div class="cell ' + stateCls + (warn.length ? " warn" : "") + '"' + (cellTitle ? ' title="' + cellTitle + '"' : "") + ">"
      + '<div class="u">' + esc(sl.n) + ' <span class="n" title="' + opts.length + ' eligible">' + opts.length + "</span></div>"
      + '<button type="button" class="' + chipCls + '" data-pn="' + esc(pn) + '" data-slot="' + slot + '" data-day="' + d + '" title="' + esc(tip) + '">'
      + '<span class="lbl">' + label + '</span><span class="arr">\u25be</span></button></div>';
  }
  function platoonHeader(pn, pidx, d) {
    const filled = platoonFillCount(pn, pidx, d);
    const sd = starDay(pn);
    const done = filled >= SLOTS;
    const early = done && sd !== null && sd > d;
    let tag = "";
    if (done) tag = '<span class="' + (early ? "early" : "maxed") + '">' + (early ? " \u26a0 early" : " \u2713") + "</span>";
    return '<h4>P' + (pidx + 1) + " <b>" + filled + "/" + SLOTS + "</b>" + tag + "</h4>";
  }
  function planetHtml(pn, d) {
    const planet = PLANET_MAP[pn];
    const sd = starDay(pn);
    let meta = "relic R" + planet.relicReq;
    meta += sd !== null ? ' \u00b7 <b>star day ' + sd + "</b>" : ' \u00b7 <span class="muted">not planned</span>';
    const pe = planEntry(pn, d);
    if (pe) meta += " \u00b7 plan goal " + Number(pe.goal) + " / P" + Number(pe.platoons || 0);
    let cols = "";
    for (let pi = 0; pi < planet.platoons.length; pi++) {
      let cells = "";
      const slots = planet.platoons[pi].slots;
      for (let s = 0; s < slots.length; s++) cells += cellHtml(pn, pi * SLOTS + s, d);
      cols += '<div class="platoon">' + platoonHeader(pn, pi, d) + cells + "</div>";
    }
    return '<details class="planet" open>'
      + '<summary><span class="pname">' + esc(pn) + '</span><span class="meta">' + meta
      + '</span><button class="gen-btn" data-mode="planet" data-day="' + d + '" data-planet="' + esc(pn) + '">auto</button></summary>'
      + '<div class="planet-body">' + cols + "</div></details>";
  }
  function warningsHtml(d) {
    const items = [];
    for (const p of activePlanets(d)) {
      const byDay = dayFills(p.name, d);
      for (const k in byDay) {
        for (const w of cellWarnings(p.name, Number(k), d, byDay[k])) items.push({ p: p.name, msg: w.msg });
      }
    }
    if (!items.length) return "";
    let html = '<div class="warnbox"><b>Conflicts on day ' + d + "</b><ul>";
    for (const it of items) html += "<li><b>" + esc(it.p) + "</b>: " + it.msg + "</li>";
    return html + "</ul></div>";
  }
  function dayStatsHtml(d) {
    let fills = 0;
    for (const p of activePlanets(d)) fills += Object.keys(dayFills(p.name, d)).length;
    return '<div class="notice">Day ' + d + ": <b>" + fills + "</b> assignments."
      + ' <button class="gen-btn" data-mode="day" data-day="' + d + '">auto</button></div>';
  }
  window.setDay = function (d) {
    state.currentDay = Number(d);
    document.getElementById("day-note").textContent = state.currentDay;
    render();
  };
  function setFill(pn, d, slot, ac) {
    slot = Number(slot);
    d = String(d);
    ac = ac ? String(ac) : "";
    state.fills[pn] = state.fills[pn] || {};
    const byDay = state.fills[pn][d] || {};
    if (ac === "") delete byDay[slot];
    else byDay[slot] = ac;
    if (Object.keys(byDay).length) state.fills[pn][d] = byDay;
    else delete state.fills[pn][d];
  }
  window.assign = function (pn, slot, d, ac) {
    setFill(pn, d, slot, ac);
    persist();
    closePicker();
    render();
  };

  // ---- picker popover ----
  function optionConflict(pn, slot, d, ac) {
    const sl = unitAt(pn, slot);
    const msgs = [];
    const adding = dayFills(pn, d)[slot] === ac ? 0 : 1;
    const cnt = countOnPlanetDay(pn, d, ac) + adding;
    if (cnt > MAX_UNITS) msgs.push(esc(memberName(ac)) + " already has " + (cnt - 1) + " fills on " + pn + " today (max " + MAX_UNITS + ")");
    if (unitAssignedOnDay(ac, sl.b, d, pn, slot)) msgs.push(esc(memberName(ac)) + " already places " + esc(sl.n) + " elsewhere today");
    return msgs.join("; ");
  }
  function pickerHtml(pn, slot, d) {
    const sl = unitAt(pn, slot);
    const opts = assigneeOptions(pn, slot);
    const cur = dayFills(pn, d)[slot];
    let html = '<div class="pop-head">' + esc(sl.n) + ' <span class="n">' + opts.length + " eligible</span></div>";
    if (cur) {
      html += '<div class="pick-row clear" data-pn="' + esc(pn) + '" data-slot="' + slot + '" data-day="' + d + '" data-ac="">Clear today\u2019s fill</div>';
    }
    for (const o of opts) {
      const conflict = optionConflict(pn, slot, d, o.ac);
      const sel = cur === o.ac;
      html += '<div class="pick-row' + (sel ? " sel" : "") + (conflict ? " dim" : "") + '"'
        + ' data-pn="' + esc(pn) + '" data-slot="' + slot + '" data-day="' + d + '" data-ac="' + o.ac + '"'
        + (conflict ? ' title="' + esc(conflict) + '"' : "") + ">"
        + (sel ? "\u2713 " : "") + esc(o.name) + '<span class="lvl">' + (o.ship ? o.level + "\u2605" : "R" + o.level) + "</span></div>";
    }
    return html;
  }
  function openPicker(btn) {
    const pn = btn.dataset.pn, slot = Number(btn.dataset.slot), d = btn.dataset.day;
    const pop = document.getElementById("picker");
    pop.innerHTML = pickerHtml(pn, slot, d);
    pop.style.display = "block";
    const r = btn.getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight;
    let left = r.right + 2;
    if (left + w > window.innerWidth - 8) left = Math.max(8, r.left - w - 2);
    let top = r.bottom + 2;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 2);
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  }
  function closePicker() {
    const pop = document.getElementById("picker");
    pop.style.display = "none";
    pop.innerHTML = "";
  }
  document.addEventListener("click", function (ev) {
    const chip = ev.target.closest(".chip");
    if (chip) { openPicker(chip); ev.preventDefault(); return; }
    const row = ev.target.closest(".pick-row");
    if (row) {
      const pn = row.dataset.pn, slot = Number(row.dataset.slot), d = row.dataset.day, ac = row.dataset.ac;
      assign(pn, slot, d, ac);
      return;
    }
    const gen = ev.target.closest(".gen-btn");
    if (gen) {
      closePicker();
      const mode = gen.dataset.mode, day = Number(gen.dataset.day), planet = gen.dataset.planet;
      openGen(mode === "planet" ? { mode: mode, day: day, planet: planet }
        : mode === "day" ? { mode: mode, day: day } : { mode: "all" });
      ev.preventDefault();
      return;
    }
    closePicker();
  });
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") closePicker(); });
  window.addEventListener("scroll", closePicker, true);
  window.addEventListener("resize", closePicker);

  // ---- auto-generation ----
  function eligibleCountForSlot(pn, slot) {
    return assigneeOptions(pn, slot).length;
  }
  // Pure member pick per strategy; candidates: [{ac, level, dayTotal, name}].
  window.genPick = function (cands, strategy) {
    if (!cands.length) return null;
    const sorted = cands.slice().sort((a, b) => {
      if (strategy === "weakest") return (a.level - b.level) || (a.dayTotal - b.dayTotal) || a.name.localeCompare(b.name);
      if (strategy === "minimize") return (a.dayTotal - b.dayTotal) || (b.level - a.level) || a.name.localeCompare(b.name);
      return (b.level - a.level) || (a.dayTotal - b.dayTotal) || a.name.localeCompare(b.name);
    });
    return sorted[0];
  };
  // How good a slot is to leave open in a preloaded platoon: leave a Galactic
  // Legend (commonly owned -> easy to fill later) and avoid constrained units.
  function openScore(pn, slot) {
    const sl = unitAt(pn, slot);
    return sl.gl * 1000 + eligibleCountForSlot(pn, slot);
  }
  function platoonTarget(pn, d) {
    const pe = planEntry(pn, d);
    if (!pe) return PLANET_MAP[pn].platoons.length; // no plan entry -> fill fully
    return clamp(Number(pe.platoons) || 0, 0, PLANET_MAP[pn].platoons.length);
  }
  // Slots to fill for a planet-day under a policy:
  //   full -> every uncovered slot; plan -> complete the plan's platoon count
  //   (platoons 1..N to 15/15) and preload the rest to 14/15.
  function slotsToFill(pn, d, policy) {
    const planet = PLANET_MAP[pn];
    const n = policy === "full" ? planet.platoons.length : platoonTarget(pn, d);
    const fill = [];
    for (let pidx = 0; pidx < planet.platoons.length; pidx++) {
      const base = pidx * SLOTS;
      const uncovered = [];
      for (let s = 0; s < SLOTS; s++) if (!latestAssignee(pn, base + s, d).ac) uncovered.push(base + s);
      if (pidx < n) {
        for (const s of uncovered) fill.push(s);
      } else {
        // preload to 14/15: leave the best slot open (GL first, then most eligible)
        uncovered.sort((a, b) => openScore(pn, b) - openScore(pn, a));
        for (const s of uncovered.slice(1)) fill.push(s);
      }
    }
    return fill;
  }
  function freshDayCtx(d) {
    const usedUnits = new Map();
    const planetCounts = new Map();
    const dayTotal = new Map();
    for (const pn in state.fills) {
      const byDay = state.fills[pn][d] || {};
      for (const k in byDay) {
        const ac = byDay[k];
        const ub = unitAt(pn, Number(k)).b;
        if (!usedUnits.has(ac)) usedUnits.set(ac, new Set());
        usedUnits.get(ac).add(ub);
        const key = pn + "\\u0000" + ac;
        planetCounts.set(key, (planetCounts.get(key) || 0) + 1);
        dayTotal.set(ac, (dayTotal.get(ac) || 0) + 1);
      }
    }
    return { usedUnits, planetCounts, dayTotal };
  }
  function eligibleCandidates(pn, slot, d, ctx) {
    const planet = PLANET_MAP[pn];
    const sl = unitAt(pn, slot);
    const ship = sl.c === 2;
    const req = planet.relicReq;
    const out = [];
    for (const m of data.members) {
      const u = m.u[sl.b];
      if (!u) continue;
      if (ship ? u[1] < SHIP_STAR : u[0] < req) continue;
      const ac = String(m.ac);
      if (ctx.usedUnits.has(ac) && ctx.usedUnits.get(ac).has(sl.b)) continue;
      if ((ctx.planetCounts.get(pn + "\\u0000" + ac) || 0) >= MAX_UNITS) continue;
      out.push({ ac: ac, name: m.name, level: ship ? u[1] : u[0], dayTotal: ctx.dayTotal.get(ac) || 0 });
    }
    return out;
  }
  window.generateAssignments = function (scope, strategy, policy) {
    policy = policy || "plan";
    strategy = strategy || "strongest";
    const days = [];
    if (scope && scope.mode === "planet") days.push({ day: scope.day, planets: [scope.planet] });
    else if (scope && scope.mode === "day") days.push({ day: scope.day, planets: activePlanets(scope.day).map(p => p.name) });
    else for (let d = 1; d <= 6; d++) days.push({ day: d, planets: activePlanets(d).map(p => p.name) });
    let added = 0;
    for (const rec of days) {
      const d = rec.day;
      const ctx = freshDayCtx(d);
      for (const pn of rec.planets) {
        const fill = slotsToFill(pn, d, policy).sort((a, b) => eligibleCountForSlot(pn, a) - eligibleCountForSlot(pn, b));
        for (const slot of fill) {
          const sl = unitAt(pn, slot);
          const pick = window.genPick(eligibleCandidates(pn, slot, d, ctx), strategy);
          if (!pick) continue;
          setFill(pn, d, slot, pick.ac);
          if (!ctx.usedUnits.has(pick.ac)) ctx.usedUnits.set(pick.ac, new Set());
          ctx.usedUnits.get(pick.ac).add(sl.b);
          const key = pn + "\\u0000" + pick.ac;
          ctx.planetCounts.set(key, (ctx.planetCounts.get(key) || 0) + 1);
          ctx.dayTotal.set(pick.ac, (ctx.dayTotal.get(pick.ac) || 0) + 1);
          added++;
        }
      }
    }
    persist();
    render();
    return added;
  };

  // ---- generation popup ----
  let genScope = null;
  window.openGen = function (scope) {
    genScope = scope || { mode: "all" };
    const label = genScope.mode === "planet"
      ? genScope.planet + " \u00b7 Day " + genScope.day
      : (genScope.mode === "day" ? "Day " + genScope.day : "All days");
    document.getElementById("gen-scope").textContent = "Scope: " + label + (genScope.mode === "all" ? " \u00b7 all planets" : "");
    document.getElementById("gen-overlay").classList.add("show");
  };
  window.closeGen = function () { document.getElementById("gen-overlay").classList.remove("show"); };
  window.generateNow = function () {
    const policy = document.querySelector('input[name="gen-policy"]:checked').value;
    const strategy = document.querySelector('input[name="gen-strategy"]:checked').value;
    const label = genScope.mode === "planet" ? genScope.planet + " \u00b7 day " + genScope.day
      : (genScope.mode === "day" ? "day " + genScope.day : "all days");
    const added = window.generateAssignments(genScope, strategy, policy);
    closeGen();
    showNotice("Generated <b>" + added + "</b> assignments for " + esc(label) + ".");
    genScope = null;
  };
  window.clearAll = function () {
    if (!window.confirm("Clear all platoon assignments? The star plan is kept.")) return;
    state.fills = {};
    persist();
    render();
    showNotice("All assignments cleared.");
  };
  function renderTabs() {
    let html = "";
    for (let d = 1; d <= 6; d++) {
      html += '<button class="tab' + (d === state.currentDay ? " on" : "") + '" onclick="setDay(' + d + ')">Day ' + d + "</button>";
    }
    document.getElementById("tabs").innerHTML = html;
  }
  function render() {
    renderTabs();
    closePicker();
    const d = state.currentDay;
    let html = dayStatsHtml(d);
    const active = activePlanets(d);
    if (!active.length) {
      html += '<div class="notice">No planets planned for day ' + d + ' — set your star goals in the <a href="./calc">calculator</a> first.</div>';
    }
    for (const p of active) html += planetHtml(p.name, d);
    document.getElementById("days").innerHTML = html;
    document.getElementById("warnings").innerHTML = warningsHtml(d);
    const sel = document.getElementById("plan-select");
    if (sel) sel.outerHTML = planControlsHtml();
  }

  // ---- export / import (JSON file, full plan: star plan + fills) ----
  function exportFills() {
    const out = {};
    for (const pn in state.fills) {
      const planet = PLANET_MAP[pn];
      if (!planet) continue;
      const days = {};
      for (const d in state.fills[pn]) {
        const list = [];
        for (const k in state.fills[pn][d]) {
          const slot = Number(k);
          const sl = planet.platoons[slotPlatoon(slot)].slots[slotPos(slot)];
          list.push([sl.n, state.fills[pn][d][k], (slotPlatoon(slot) + 1) + ":" + slotPos(slot)]);
        }
        list.sort((a, b) => a[2].localeCompare(b[2], undefined, { numeric: true }));
        days[d] = list;
      }
      out[pn] = days;
    }
    return out;
  }
  window.exportPayload = function () {
    persist();
    return {
      format: "swgoh-plan", v: 1,
      g: data.guildId, name: planName(),
      deployPct: state.deployPct, unlockZeffo: state.unlockZeffo, unlockMandalore: state.unlockMandalore,
      days: state.days, fills: exportFills(),
    };
  };
  window.exportPlan = function () {
    const payload = window.exportPayload();
    const json = JSON.stringify(payload);
    const blob = new Blob([json], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (data.guildName || data.guildId) + "-" + String(payload.name).replace(/[^\\w.-]+/g, "_") + ".json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  };
  function showNotice(msg) {
    document.getElementById("notice").innerHTML = '<div class="notice">' + msg + "</div>";
  }
  window.importPlanFile = function (input) {
    const f = input.files && input.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = function () {
      try {
        importPlan(JSON.parse(reader.result));
      } catch (e) {
        showNotice("Could not read plan file: " + esc(e.message));
      }
      input.value = "";
    };
    reader.readAsText(f);
  };
  window.importPlan = importPlan;
  function importPlan(p) {
    if (!p || p.format !== "swgoh-plan") throw new Error("not a swgoh-plan file");
    const days = {};
    for (const d in (p.days || {})) {
      days[d] = {};
      for (const pn in p.days[d]) {
        if (!PLANET_MAP[pn]) continue;
        const g = p.days[d][pn] || {};
        days[d][pn] = {
          goal: String(clamp(Number(g.goal) || 0, 0, 3)),
          platoons: clamp(Number(g.platoons) || 0, 0, 6),
          cmPct: clamp(Number(g.cmPct) || 0, 0, 100),
        };
      }
    }
    const fills = {};
    const unresolved = [];
    let skipped = 0;
    for (const pn in (p.fills || {})) {
      const planet = PLANET_MAP[pn];
      if (!planet) continue;
      const byDay = {};
      for (const d in p.fills[pn]) {
        const map = {};
        for (const entry of p.fills[pn][d]) {
          const ac = String(entry[1]);
          if (!MEMBER_MAP[ac]) { unresolved.push(ac); continue; }
          const parts = String(entry[2]).split(":");
          const slot = (Number(parts[0]) - 1) * SLOTS + Number(parts[1]);
          const pl = planet.platoons[slotPlatoon(slot)];
          if (!pl || !pl.slots[slotPos(slot)]) { skipped++; continue; }
          map[slot] = ac;
        }
        if (Object.keys(map).length) byDay[d] = map;
      }
      if (Object.keys(byDay).length) fills[pn] = byDay;
    }
    state.deployPct = clamp(Number(p.deployPct) || 100, 0, 100);
    state.unlockZeffo = !!p.unlockZeffo;
    state.unlockMandalore = !!p.unlockMandalore;
    state.days = days;
    state.fills = fills;
    const name = String(p.name || "Imported");
    const plans = loadPlans();
    plans[name] = { deployPct: state.deployPct, unlockZeffo: state.unlockZeffo, unlockMandalore: state.unlockMandalore, days: days, fills: fills };
    localStorage.setItem(LS_KEY, JSON.stringify(plans));
    localStorage.setItem(LS_CURRENT, name);
    let note = "Imported plan <b>" + esc(name) + "</b>.";
    if (unresolved.length) note += " Unknown members (not in this roster): " + esc([...new Set(unresolved)].join(", ")) + ".";
    if (skipped) note += " Skipped " + skipped + " fill(s) whose slot no longer exists.";
    showNotice(note);
    render();
  }

  loadPlan(planName());
  render();
})();
</script>
</body>
</html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("guild_id", nargs="?", default=DEFAULT_GUILD)
    parser.add_argument("--outdir", type=Path, default=data_root())
    args = parser.parse_args(argv)

    outdir = args.outdir
    rote_path = outdir / "rote" / f"{TB_ID}.json"
    summary_path = outdir / "guilds" / f"{args.guild_id}.summary.json"
    for p in (rote_path, summary_path):
        if not p.exists():
            print(f"missing {p}; run rote.py / guild_summary.py first", file=sys.stderr)
            return 2

    data = build_data(outdir, args.guild_id)
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = Environment(autoescape=False).from_string(HTML_TEMPLATE).render(
        guild_name=data["guildName"],
        data_json=data_json,
        SLOTS_PER_PLATOON=SLOTS_PER_PLATOON,
        MAX_UNITS_PER_MEMBER=MAX_UNITS_PER_MEMBER,
        SHIP_STAR_REQ=SHIP_STAR_REQ,
    )
    outpath = outdir / "guilds" / f"{args.guild_id}.platoons.html"
    outpath.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(outpath, html)
    print(f"wrote {outpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
