#!/usr/bin/env python3
"""ROTE day-by-day star calculator — interactive self-contained HTML page.

Builds the page from data/rote/t05D.json (planets, star thresholds, CM points,
platoon rewards) plus the guild roster GP.

Model (aggregate, per day):
  minTP = sum of the star-goal thresholds (0 for preloaded planets)
  maxTP = minTP + sum(star-1 threshold - 1) over preloaded planets
  CM needed min = minTP - prevPreload - platoon - deploy
  CM needed max = maxTP - prevPreload - platoon - deploy
  (as % of the day's total CM points across accessible planets)
  carry to next day = clamp(earned - (minTP - prevPreload), 0, preload capacity)

Usage:
    python rote_calc.py
    python rote_calc.py NW4t0-dBRcG8n-PVhykpKg
"""

import argparse
import json
import re
import sys
from pathlib import Path

from jinja2 import Environment

from swgoh_reviewer.config import data_root
from swgoh_reviewer.io import atomic_write_text

DEFAULT_GUILD = "NW4t0-dBRcG8n-PVhykpKg"
TB_ID = "t05D"
CM_MULTIPLIER = 50


def parse_num(s):
    m = re.match(r"([\d.]+)\s*([MK]?)", (s or "").strip())
    if not m:
        return 0
    v = float(m.group(1))
    if m.group(2) == "M":
        v *= 1e6
    if m.group(2) == "K":
        v *= 1e3
    return int(v)


def build_data(outdir, guild_id, tb_id=TB_ID):
    rote = json.loads((outdir / "rote" / f"{tb_id}.json").read_text())
    summary = json.loads((outdir / "guilds" / f"{guild_id}.summary.json").read_text())
    guild_gp = sum(int(m.get("galacticPower") or 0) for m in summary.get("members", []))

    chains = {"light": [], "dark": [], "neutral": []}
    specials = {"zeffo": None, "mandalore": None}
    for ph in rote.get("phases", []):
        for p in ph.get("planets", []):
            if not p.get("op"):
                continue
            m = re.search(r"conflict(\d+)(_bonus)?", p.get("planetId") or "")
            if not m:
                continue
            idx, bonus = int(m.group(1)), bool(m.group(2))
            reward = parse_num((p["op"]["platoons"][0] or {}).get("reward")) if p["op"].get("platoons") else 0
            cm = sum(sum(m2.get("pointsPerWave") or []) for m2 in p.get("missions", [])) * CM_MULTIPLIER
            rec = {
                "name": p["name"],
                "phase": ph.get("phase"),
                "relicReq": (p.get("op") or {}).get("relicRequirement"),
                "thresholds": [int(x) for x in p.get("starThresholds") or [0, 0, 0]],
                "cmMax": cm,
                "platoonReward": reward,
                "platoonsTotal": len(p["op"].get("platoons") or []),
            }
            if bonus:
                key = "zeffo" if idx == 1 else ("mandalore" if idx == 3 else None)
                if key and specials[key] is None:
                    specials[key] = rec
            elif idx == 1:
                chains["light"].append(rec)
            elif idx == 2:
                chains["dark"].append(rec)
            elif idx == 3:
                chains["neutral"].append(rec)

    return {
        "guildName": summary.get("guildName", guild_id),
        "guildGp": guild_gp,
        "chains": [
            {"id": "light", "name": "Light Side", "planets": chains["light"]},
            {"id": "dark", "name": "Dark Side", "planets": chains["dark"]},
            {"id": "neutral", "name": "Neutral", "planets": chains["neutral"]},
        ],
        "specials": [
            {"id": "zeffo", "name": "Zeffo", "chain": "light", "triggerIndex": 2, "triggerName": "Bracca", "planet": specials["zeffo"]},
            {"id": "mandalore", "name": "Mandalore", "chain": "neutral", "triggerIndex": 3, "triggerName": "Tatooine", "planet": specials["mandalore"]},
        ],
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ROTE star calculator — {{ guild_name }}</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; background: #fafafa; color: #222; }
  header { background: #1c2541; color: #fff; padding: 12px 20px; }
  header h1 { margin: 0; font-size: 18px; }
  header .sub { font-size: 12px; opacity: .9; margin-top: 4px; }
  main { padding: 16px 20px; max-width: 1100px; margin: 0 auto; }
  .day { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 12px 14px; margin-bottom: 16px; }
  .day h3 { margin: 0 0 10px; }
  .daygrid { display: flex; gap: 14px; align-items: flex-start; flex-wrap: wrap; }
  .labels-col { width: 92px; flex: 0 0 92px; }
  .labels-col .lab-spacer { height: 26px; }
  .labels-col .lab { height: 34px; line-height: 34px; font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: .03em; }
  .planets-col { display: flex; flex-direction: column; gap: 12px; flex: 1 1 auto; min-width: 0; }
  .prow-pl { display: flex; gap: 10px; flex-wrap: wrap; }
  .prow-pl.specials { border-top: 2px dashed #d5dbe7; padding-top: 12px; }
  .pcol { min-width: 205px; }
  .pcol .pname { height: 26px; line-height: 26px; font-weight: 700; font-size: 13px; }
  .pcol .ctl { height: 34px; display: flex; align-items: center; width: 185px; }
  .overall { border: 1px solid #c9d2e3; border-radius: 8px; padding: 8px 12px; background: #f4f6fb; flex: 0 0 280px; font-size: 13px; }
  .overall .metric { display: flex; justify-content: space-between; align-items: baseline; padding: 4px 0; border-bottom: 1px solid #e3e8f2; }
  .overall .metric:last-child { border-bottom: none; }
  .overall .metric span { color: #667; font-size: 11px; text-transform: uppercase; letter-spacing: .03em; }
  .overall .metric b { font-size: 13px; }
  .overall .metric.bad b { color: #b71c1c; }
  .overall-bottom { margin-top: 10px; padding-top: 8px; border-top: 1px solid #e5e7eb; font-size: 12px; color: #555; }
  .detail { color: #666; font-size: 12px; margin-top: 3px; }
  .warn { color: #b71c1c; font-weight: 700; margin-top: 4px; }
  .muted { color: #888; }
  .badge { display:inline-block; padding:1px 7px; border-radius:9px; font-size:11px; font-weight:700; background:#eef0f5; color:#333; }
  .gopt { position: relative; border: 1px solid #ccc; border-radius: 6px; padding: 2px 8px; cursor: pointer; font-size: 13px; min-width: 26px; text-align: center; user-select: none; }
  .gopt input { position: absolute; opacity: 0; pointer-events: none; }
  .gopt.on { background: #1c2541; border-color: #1c2541; }
  .stars { color: #f9a825; letter-spacing: 0; }
  .gopt.on .stars, .gopt.on .muted { color: #fff; }
  .seg { display: flex; width: 100%; border: 1px solid #ccc; border-radius: 6px; overflow: hidden; }
  .seg .gopt { border: none; border-radius: 0; flex: 1 1 0; min-width: 0; padding: 2px 0; }
  .seg .gopt + .gopt { border-left: 1px solid #ccc; }
  .seg .gopt.on { background: #1c2541; color: #fff; }
  .seg .gopt.on + .gopt { border-left-color: #1c2541; }
  .seg .gopt.disabled { opacity: .35; pointer-events: none; }
  .box { color: #556; }
  .gopt.on .box { color: #fff; }
  .gift { font-size: 13px; }
  .pcol input[type=range] { width: 130px; }
  .cmval { font-size: 12px; color: #444; margin-left: 4px; }
  .controls { margin: 12px 0; }
  .unlockrow { margin-top: 8px; font-size: 13px; color: #444; }
  .summary { margin: 6px 0 20px; padding: 10px 12px; background: #eef2fa; border: 1px solid #c9d2e3; border-radius: 8px; font-size: 14px; }
  .modal-overlay { position: fixed; inset: 0; background: rgba(15,20,35,.55); display: flex; align-items: flex-start; justify-content: center; padding: 40px 16px; z-index: 50; overflow-y: auto; }
  .modal { background: #fff; border-radius: 10px; max-width: 780px; width: 100%; box-shadow: 0 12px 40px rgba(0,0,0,.35); }
  .mhead { background: #1c2541; color: #fff; padding: 12px 18px; border-radius: 10px 10px 0 0; display: flex; justify-content: space-between; align-items: center; }
  .mhead h2 { margin: 0; font-size: 16px; }
  .mhead button { background: none; border: none; color: #fff; font-size: 18px; cursor: pointer; }
  .mbody { padding: 12px 18px; max-height: 65vh; overflow-y: auto; }
  .mfoot { padding: 10px 18px; display: flex; gap: 8px; border-top: 1px solid #e5e7eb; justify-content: flex-end; flex-wrap: wrap; }
  .est-row { display: flex; align-items: center; gap: 8px; padding: 5px 0; font-size: 13px; }
  .est-row .nm { width: 150px; flex: 0 0 150px; }
  .est-row input[type=range] { flex: 0 1 auto; width: 130px; min-width: 60px; }
  .est-row .pv { width: 44px; flex: 0 0 44px; text-align: right; font-variant-numeric: tabular-nums; }
  .est-row select { flex: 0 0 52px; font-size: 12px; }
  .opt-mode { display: flex; gap: 6px; margin-bottom: 10px; font-size: 13px; }
  .opt-mode label { border: 1px solid #ccc; border-radius: 6px; padding: 2px 10px; cursor: pointer; user-select: none; }
  .opt-mode label.on { background: #1c2541; color: #fff; border-color: #1c2541; }
  .est-group { margin-bottom: 6px; }
  .est-phase { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 700; padding: 6px 0 2px; }
  .est-phase select { font-size: 12px; }
  .est-planet { display: flex; align-items: center; gap: 8px; padding: 2px 0 2px 12px; font-size: 12px; }
  .est-planet .nm2 { width: 210px; flex: 0 0 210px; }
  .est-planet input[type=range] { flex: 0 1 auto; width: 130px; min-width: 60px; }
  .est-planet .pv { width: 44px; flex: 0 0 44px; text-align: right; font-variant-numeric: tabular-nums; }
  .est-row .planets { flex: 1 1 auto; font-size: 11px; color: #667; min-width: 0; }
  .day.bad { border-color: #b71c1c; }
  .day.low { border-color: #f9a825; }
  .day-banner { padding: 6px 10px; border-radius: 6px; font-size: 12px; margin-bottom: 8px; }
  .day-banner:empty { display: none; }
  .day-banner.bad { background: #ffcdd2; color: #b71c1c; }
  .day-banner.low { background: #fff3cd; color: #7a5b00; }
  .opt-row { margin: 8px 0; font-size: 13px; }
  .opt-result { margin-top: 10px; padding: 10px 12px; background: #eef2fa; border: 1px solid #c9d2e3; border-radius: 8px; font-size: 13px; }
  .opt-line { margin-bottom: 6px; font-weight: 700; }
  .dayline { font-size: 12px; color: #444; margin: 3px 0; }
  .opt-warn { color: #b71c1c; font-weight: 700; margin-top: 6px; }
</style>
</head>
<body>
<header>
  <h1>ROTE day-by-day star calculator — {{ guild_name }}</h1>
  <div class="sub">
    Guild GP <b id="guild-gp"></b> ·
    plan <select id="plan-select" onchange="selectPlan()"></select>
    <button onclick="openNewPlan()">New Plan</button>
    <button id="share-btn" onclick="sharePlan()" title="Copy a link that opens this plan">Share</button>
    <button onclick="openPlanner()" title="Plan which member fills each platoon slot, day by day">Open planner</button>
    <button onclick="openOpt()">Optimize</button>
    &nbsp;&nbsp; total stars: <b id="total-stars">0</b>
    <label style="float:right" title="Show scores as e.g. 1.2B / 234.3M; tooltips keep exact values">
      <input type="checkbox" id="compact-toggle" onchange="toggleCompact(this)"> compact numbers
    </label>
  </div>
</header>
<main>
  <div class="controls">
    <label>Deploy: <input id="deploy" type="range" min="0" max="100" value="100" oninput="deployInput(this)">
      <b><span id="deploy-val">100%</span></b></label>
  </div>
  <div class="muted" style="margin-bottom:10px">
    Per day you pick each accessible planet's action. The page reports the min % of that day's combat-mission
    points needed to hit your star goals, the max % you can earn without accidentally starring a preloaded
    planet, and how many points carry over to the next day.
  </div>
  <div id="days"></div>
  <div id="summary"></div>
</main>
<div class="modal-overlay" id="np-overlay" style="display:none">
  <div class="modal" style="max-width:440px">
    <header class="mhead">
      <h2>New plan</h2>
      <button onclick="closeNewPlan()">&#215;</button>
    </header>
    <div class="mbody">
      <div class="opt-row">
        <label>Name: <input id="np-name" placeholder="plan name" size="24"
          onkeydown="if(event.key==='Enter')createNewPlan()"></label>
      </div>
      <div class="opt-row">
        <label><input type="radio" name="np-type" value="dup" checked> Duplicate current plan</label>
      </div>
      <div class="opt-row">
        <label><input type="radio" name="np-type" value="blank"> Blank new plan</label>
      </div>
      <div class="muted" style="font-size:12px">
        The current plan is saved automatically as you change it; "New plan" only creates another plan to work on.
      </div>
    </div>
    <div class="mfoot">
      <button onclick="closeNewPlan()">Cancel</button>
      <button onclick="createNewPlan()">Create</button>
    </div>
  </div>
</div>
<div class="modal-overlay" id="opt-overlay" style="display:none">
  <div class="modal">
    <header class="mhead">
      <h2>Plan optimizer</h2>
      <button onclick="closeOpt()">&#215;</button>
    </header>
    <div class="mbody">
      <div class="muted" style="font-size:12px;margin-bottom:8px">
        Estimate CM% either <b>by level</b> (one slider per relic tier, all planets in a phase share it) or
        <b>by planet</b> (each planet its own; Zeffo counts in level 3, Mandalore in level 4). A planet keeps
        earning its CM% on every day it stays accessible. The <b>P</b> dropdown per phase sets how many platoons
        you expect to fill on each planet when it is starred (default 6). The optimizer finds the max-star plan
        achievable at these inputs. Unchecked specials are skipped; checked specials are maxed (3&#9733;) by the
        end of the event. Estimates, mode and platoon expectations are remembered in this browser.
      </div>
      <div id="opt-est"></div>
      <div class="opt-row">
        <label><input type="checkbox" id="opt-unlock-zeffo" onchange="optSetUnlock('zeffo', this.checked)"> Unlock Zeffo and max it (3&#9733;)</label>
        &nbsp;&nbsp;
        <label><input type="checkbox" id="opt-unlock-mandalore" onchange="optSetUnlock('mandalore', this.checked)"> Unlock Mandalore and max it (3&#9733;)</label>
      </div>
      <div class="opt-row">
        <label>Deploy: <input id="opt-deploy" type="range" min="0" max="100" step="1" value="100" oninput="optSetDeploy(this)">
          <b><span id="opt-deploy-val">100%</span></b></label>
        <span class="muted"> &middot; guild GP <span id="opt-gp"></span></span>
      </div>
      <div class="opt-result" id="opt-result" style="display:none"></div>
    </div>
    <div class="mfoot">
      <button onclick="optResetEst()">Reset estimates to defaults</button>
      <button onclick="runOpt()">Run optimizer</button>
      <button onclick="applyOpt()" id="opt-apply" disabled>Apply plan</button>
    </div>
  </div>
</div>
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
  const LS_COMPACT = "roteCalcCompact";
  const state = { deployPct: 100, unlockZeffo: false, unlockMandalore: false, days: {} };
  let compact = true;
  let lastFocusId = null;

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function esc(s) { return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
  function pfmt(n) { return Number(n || 0).toLocaleString(); }
  function fmt(n) {
    const v = Number(n || 0);
    if (!compact) return pfmt(v);
    const abs = Math.abs(v);
    for (const m of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
      if (abs >= m[0]) return (v / m[0]).toFixed(1).replace(/\\.0$/, "") + m[1];
    }
    return String(Math.round(v));
  }

  function planName() { return localStorage.getItem(LS_CURRENT) || "Default"; }
  function loadPlans() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {}; } catch (e) { return {}; } }
  function loadCompact() { try { const v = localStorage.getItem(LS_COMPACT); return v === null ? true : v === "1"; } catch (e) { return true; } }
  function persist() {
    try {
      const plans = loadPlans();
      const prev = plans[planName()] || {};
      plans[planName()] = Object.assign({}, prev, {
        deployPct: state.deployPct, unlockZeffo: state.unlockZeffo,
        unlockMandalore: state.unlockMandalore, days: state.days,
      });
      localStorage.setItem(LS_KEY, JSON.stringify(plans));
    } catch (e) { /* storage unavailable */ }
  }
  window.openPlanner = function () {
    persist();
    location.href = "/g/" + guildId + "/platoons";
  };
  function loadPlan(name) {
    const saved = loadPlans()[name] || {};
    state.deployPct = saved.deployPct !== undefined ? saved.deployPct : 100;
    state.unlockZeffo = !!saved.unlockZeffo;
    state.unlockMandalore = !!saved.unlockMandalore;
    state.days = saved.days || {};
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

  // ---- sharing a plan as a URL-encoded payload (no server storage) ----
  function planJson() {
    return JSON.stringify({
      deployPct: state.deployPct,
      unlockZeffo: state.unlockZeffo,
      unlockMandalore: state.unlockMandalore,
      days: state.days,
    });
  }
  function encodePlan() {
    const bytes = new TextEncoder().encode(planJson());
    let bin = "";
    for (const b of bytes) bin += String.fromCharCode(b);
    return btoa(bin).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/, "");
  }
  function decodePlan(enc) {
    try {
      const b64 = String(enc).replace(/-/g, "+").replace(/_/g, "/");
      const bin = atob(b64);
      const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
      return JSON.parse(new TextDecoder().decode(bytes));
    } catch (e) { return null; }
  }
  window.sharePlan = function () {
    persist();
    const url = location.origin + location.pathname + "?plan=" + encodePlan();
    const btn = document.getElementById("share-btn");
    const flash = () => {
      const old = btn.textContent;
      btn.textContent = "Copied!";
      setTimeout(() => { btn.textContent = old; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(flash, () => { window.prompt("Copy this plan link:", url); });
    } else {
      window.prompt("Copy this plan link:", url);
    }
  };
  function loadSharedPlan() {
    const params = new URLSearchParams(location.search);
    const planId = params.get("planId");
    if (planId) {
      // load a server plan by id (short share link); async
      if (typeof fetch === "function") {
        fetch("/g/" + guildId + "/plans").then(r => r.json()).then(d => {
          const p = ((d && d.plans) || []).find(x => String(x.id) === String(planId));
          if (p && p.payload) {
            state.deployPct = p.payload.deployPct !== undefined ? p.payload.deployPct : 100;
            state.unlockZeffo = !!p.payload.unlockZeffo;
            state.unlockMandalore = !!p.payload.unlockMandalore;
            state.days = p.payload.days || {};
            try { localStorage.setItem(LS_CURRENT, p.name || "Shared"); } catch (e) { /* ignore */ }
            persist();
            render();
          }
        }).catch(() => {});
      }
      return true;
    }
    const enc = params.get("plan");
    const shared = enc ? decodePlan(enc) : null;
    if (!shared) return false;
    state.deployPct = shared.deployPct !== undefined ? shared.deployPct : 100;
    state.unlockZeffo = !!shared.unlockZeffo;
    state.unlockMandalore = !!shared.unlockMandalore;
    state.days = shared.days || {};
    try { localStorage.setItem(LS_CURRENT, "Shared"); } catch (e) { /* ignore */ }
    persist();
    return true;
  }
  function loadGuildPlan() {
    // start from the guild's published plan (days) when there's no explicit share link
    if (typeof fetch !== "function") return;
    fetch("/g/" + guildId + "/plan").then(r => r.json()).then(d => {
      if (d && d.plan) {
        const p = d.plan.payload || {};
        state.deployPct = p.deployPct !== undefined ? p.deployPct : 100;
        state.unlockZeffo = !!p.unlockZeffo;
        state.unlockMandalore = !!p.unlockMandalore;
        state.days = p.days || {};
        try { localStorage.setItem(LS_CURRENT, d.plan.name); } catch (e) { /* ignore */ }
        persist();
        render();
      }
    }).catch(() => {});
  }

  const PLANET_ORDER = { dark: 0, neutral: 1, light: 2, zeffo: 3, mandalore: 4 };
  function planetOrder(row) { return PLANET_ORDER[row.a.special || row.a.chain] ?? 9; }
  function triggerPlanetName(sp) {
    const ch = data.chains.find(c => c.id === sp.chain);
    return ch ? (ch.planets[sp.triggerIndex - 1] || {}).name : null;
  }

  function goalBarHtml(day, key, goal, special, planet) {
    const cur = goal === "0" ? "0" : String(goal);
    const th = planet ? planet.thresholds : [0, 0, 0];
    const opts = special
      ? [["0", '<span class="muted">0</span>', "Preload (no star)"],
         ["1", '<span class="gift">&#127873;</span>', "1★ gift: " + pfmt(th[0]) + " pts"],
         ["2", '<span class="gift">&#127873;&#127873;</span>', "2★ gift: " + pfmt(th[1]) + " pts"],
         ["3", '<span class="stars">&#9733;</span>', "3★: " + pfmt(th[2]) + " pts"]]
      : [["0", '<span class="muted">0</span>', "Preload (no star)"],
         ["1", '<span class="stars">&#9733;</span>', "1★: " + pfmt(th[0]) + " pts"],
         ["2", '<span class="stars">&#9733;&#9733;</span>', "2★: " + pfmt(th[1]) + " pts"],
         ["3", '<span class="stars">&#9733;&#9733;&#9733;</span>', "3★: " + pfmt(th[2]) + " pts"]];
    return '<div class="seg" role="radiogroup">' + opts.map(o => {
      const v = o[0];
      return '<label class="gopt' + (cur === v ? " on" : "") + '" title="' + esc(o[2]) + '"><input type="radio" name="d' + day + "-" + key +
        '" value="' + v + '"' + (cur === v ? " checked" : "") + ' onchange="setGoal(this.name,this.value)">' +
        o[1] + "</label>";
    }).join("") + "</div>";
  }

  function platoonBarHtml(day, key, plats, remaining) {
    let html = '<div class="seg" role="radiogroup">';
    for (let n = 0; n <= 6; n++) {
      const disabled = n > remaining;
      html += '<label class="gopt' + (plats === n ? " on" : "") + (disabled ? " disabled" : "") + '">' +
        '<input type="radio" name="d' + day + "-" + key + '-plats" value="' + n + '"' +
        (plats === n ? " checked" : "") + (disabled ? " disabled" : "") +
        ' onchange="setPlatoon(this.name,this.value)">' + n + "</label>";
    }
    return html + "</div>";
  }

  function planetCardHtml(r, row) {
    const key = row.a.planet.name;
    const tag = row.a.special ? ' <span class="badge">' + esc(row.a.special) + "</span>" : "";
    return '<div class="pcol"><div class="pname">' + esc(key) + tag + "</div>" +
      '<div class="ctl">' + goalBarHtml(r.day, key, row.goal, !!row.a.special, row.a.planet) + "</div>" +
      '<div class="ctl">' + platoonBarHtml(r.day, key, row.plats, row.remaining) + "</div>" +
      '<div class="ctl"><input id="d' + r.day + "-" + key + '-cm" type="range" min="0" max="100" step="1" value="' +
      row.cp + '" oninput="cmInput(this)"> <span id="d' + r.day + "-" + key + '-cmval" class="cmval">' + row.cp + "%</span></div></div>";
  }

  const LABELS = '<div class="labels-col"><div class="lab-spacer"></div>' +
    '<div class="lab">Star goal</div><div class="lab">Platoons</div><div class="lab">CM%</div></div>';

  function compute() {
    const chainIdx = { light: 0, dark: 0, neutral: 0 };
    const filledPlatoon = {};
    const done = {};
    const unlocked = { zeffo: false, mandalore: false };
    const chainStars = { light: 0, dark: 0, neutral: 0, zeffo: 0, mandalore: 0 };
    const unlockShown = {};
    let totalStars = 0;
    let bankIn = 0;
    const days = [];

    for (let d = 1; d <= 6; d++) {
      const unlockToggles = [];
      for (const sp of data.specials) {
        if (!done[sp.id] && (sp.id === "zeffo" ? state.unlockZeffo : state.unlockMandalore) &&
            chainIdx[sp.chain] >= sp.triggerIndex) {
          unlocked[sp.id] = true;
        }
      }
      const accessible = [];
      for (const ch of data.chains) {
        const p = ch.planets[chainIdx[ch.id]];
        if (p) accessible.push({ planet: p, chain: ch.id, special: null });
      }
      for (const sp of data.specials) {
        if (unlocked[sp.id] && !done[sp.id]) accessible.push({ planet: sp.planet, chain: sp.chain, special: sp.id });
      }

      const inputs = state.days[d] || {};
      let minTP = 0, maxTP = 0, platoon = 0, estCM = 0, totalCM = 0;
      const rows = [];
      for (const a of accessible) {
        const p = a.planet;
        const inp = inputs[p.name] || {};
        const goal = inp.goal === undefined ? "0" : String(inp.goal);
        const action = goal === "0" ? "preload" : "finish";
        const stars = goal === "0" ? 1 : clamp(Number(goal) || 1, 1, 3);
        const remaining = Math.max(0, (p.platoonsTotal || 6) - (filledPlatoon[p.name] || 0));
        const plats = clamp(inp.platoons || 0, 0, remaining);
        const cp = inp.cmPct || 0;
        totalCM += p.cmMax;
        if (action === "finish") {
          const th = p.thresholds[stars - 1] || 0;
          minTP += th; maxTP += th;
        } else {
          maxTP += (p.thresholds[0] - 1);
        }
        platoon += plats * p.platoonReward;
        estCM += cp / 100 * p.cmMax;
        rows.push({ a, action, stars, goal, plats, cp, remaining });
      }

      const gp = state.deployPct / 100 * data.guildGp;
      const cmMinPts = minTP - bankIn - platoon - gp;
      const cmMaxPts = maxTP - bankIn - platoon - gp;
      const minPct = totalCM ? clamp(cmMinPts / totalCM * 100, 0, 100) : 0;
      const maxPct = totalCM ? clamp(cmMaxPts / totalCM * 100, 0, 100) : 0;
      const capacity = Math.max(0, maxTP - minTP);
      const carry = clamp(bankIn + gp + platoon + estCM - minTP, 0, capacity);
      const wastedGp = clamp(bankIn + gp + platoon + estCM - maxTP, 0, gp);
      const feasible = cmMinPts <= totalCM;
      const estPct = totalCM ? estCM / totalCM * 100 : 0;
      const shortEst = !feasible ? false : cmMinPts > estCM + 0.5;
      const shortPts = cmMinPts - estCM;

      for (const r of rows) {
        if (r.action === "finish") {
          const credit = r.a.special ? (r.stars === 3 ? 1 : 0) : r.stars;
          if (r.a.special) { done[r.a.special] = true; chainStars[r.a.special] += credit; }
          else {
            chainIdx[r.a.chain]++; chainStars[r.a.chain] += credit;
            for (const sp of data.specials) {
              if (!unlockShown[sp.id] && !done[sp.id] && r.a.planet.name === triggerPlanetName(sp)) {
                unlockShown[sp.id] = true;
                unlockToggles.push({ id: sp.id, name: sp.name, trigger: sp.triggerName });
              }
            }
          }
        }
      }
      for (const r of rows) {
        filledPlatoon[r.a.planet.name] = (filledPlatoon[r.a.planet.name] || 0) + r.plats;
      }
      totalStars = chainStars.light + chainStars.dark + chainStars.neutral + chainStars.zeffo + chainStars.mandalore;
      days.push({
        day: d, rows, minTP, maxTP, platoon, estCM, totalCM, gp, bankIn, cmMinPts,
        minPct, maxPct, estPct, carry, wastedGp, feasible, shortEst, shortPts,
        totalStars, chainStars: Object.assign({}, chainStars),
        unlockToggles, unlocked: Object.assign({}, unlocked),
      });
      bankIn = carry;
    }
    return days;
  }

  function metricsHtml(r) {
    const min = r.feasible ? r.minPct : (r.totalCM ? r.cmMinPts / r.totalCM * 100 : 0);
    return '<div class="metric' + (r.feasible ? "" : " bad") + '"><span>min CM</span><b>' + min.toFixed(1) + "%</b></div>" +
      '<div class="metric"><span>max CM</span><b>' + r.maxPct.toFixed(1) + "%</b></div>" +
      '<div class="metric"><span>bank in</span><b>' + fmt(r.bankIn) + "</b></div>" +
      '<div class="metric"><span>carry out</span><b>' + fmt(r.carry) + "</b></div>" +
      '<div class="metric"><span>wasted deploy</span><b>' + fmt(r.wastedGp) + "</b></div>";
  }

  function bottomHtml(r) {
    return '<div class="detail">minTP ' + fmt(r.minTP) + " · maxTP " + fmt(r.maxTP) +
      " · platoons " + fmt(r.platoon) + " · deploy " + fmt(r.gp) + " · CM avail " + fmt(r.totalCM) + "</div>" +
      (r.feasible ? "" : '<div class="warn">&#9888; goals exceed deploy + platoons + 100% CM</div>');
  }

  function statusHtml(r) {
    if (!r.feasible) {
      return '<div class="day-banner bad">&#9888; <b>Cannot reach these goals.</b> Even 100% CM is not enough — still short by ' +
        fmt(Math.round(r.cmMinPts - r.totalCM)) + " points.</div>";
    }
    if (r.shortEst) {
      return '<div class="day-banner low">&#9888; <b>Estimated CM falls short.</b> Your sliders give ~' + r.estPct.toFixed(0) +
        "% of the day CM, but these goals need " + r.minPct.toFixed(1) + "% — short by " +
        fmt(Math.round(r.shortPts)) + " points.</div>";
    }
    return "";
  }

  function summaryHtml(last) {
    const c = last.chainStars;
    return '<div class="summary"><b>Total stars: ' + last.totalStars + '</b> &nbsp;·&nbsp; Light ' + c.light +
      " · Dark " + c.dark + " · Neutral " + c.neutral + " · Zeffo " + c.zeffo + " · Mandalore " + c.mandalore + "</div>";
  }

  function render() {
    const days = compute();
    const last = days[days.length - 1];
    document.getElementById("deploy").value = state.deployPct;
    document.getElementById("deploy-val").textContent = state.deployPct + "%";
    document.getElementById("total-stars").textContent = last.totalStars;
    document.getElementById("guild-gp").textContent = fmt(data.guildGp);
    document.getElementById("compact-toggle").checked = compact;
    document.getElementById("plan-select").outerHTML = planControlsHtml();
    document.getElementById("summary").innerHTML = summaryHtml(last);

    let html = "";
    for (const r of days) {
      const all = r.rows.slice().sort((x, y) => planetOrder(x) - planetOrder(y));
      const standard = all.filter(x => !x.a.special);
      const specials = all.filter(x => x.a.special);
      const cls = !r.feasible ? " bad" : (r.shortEst ? " low" : "");
      html += '<section class="day' + cls + '" id="daysec-' + r.day + '"><h3>Day ' + r.day + '</h3>' +
        '<div class="day-banner" id="banner-' + r.day + '">' + statusHtml(r) + '</div><div class="daygrid">' + LABELS + '<div class="planets-col">';
      if (standard.length) html += '<div class="prow-pl">' + standard.map(row => planetCardHtml(r, row)).join("") + "</div>";
      if (specials.length) html += '<div class="prow-pl specials">' + specials.map(row => planetCardHtml(r, row)).join("") + "</div>";
      html += '</div><div class="overall" id="res-' + r.day + '">' + metricsHtml(r) + "</div></div>";
      if (r.unlockToggles.length) {
        html += '<div class="unlockrow">' + r.unlockToggles.map(t => {
          const checked = t.id === "zeffo" ? state.unlockZeffo : state.unlockMandalore;
          return '<label><input type="checkbox" id="unlock-' + t.id + '"' + (checked ? " checked" : "") +
            ' onchange="setVal(this.id, this.checked)"> Unlock ' + esc(t.name) + ' (after ' + esc(t.trigger) + ")</label>";
        }).join(" ") + "</div>";
      }
      html += '<div class="overall-bottom" id="resb-' + r.day + '">' + bottomHtml(r) + "</div></section>";
    }
    document.getElementById("days").innerHTML = html;
    persist();
    if (lastFocusId) {
      const el = document.getElementById(lastFocusId);
      if (el) { el.focus(); }
    }
  }

  function updateResults() {
    const days = compute();
    const last = days[days.length - 1];
    document.getElementById("deploy-val").textContent = state.deployPct + "%";
    document.getElementById("total-stars").textContent = last.totalStars;
    document.getElementById("summary").innerHTML = summaryHtml(last);
    for (const r of days) {
      const el = document.getElementById("res-" + r.day);
      if (el) el.innerHTML = metricsHtml(r);
      const b = document.getElementById("resb-" + r.day);
      if (b) b.innerHTML = bottomHtml(r);
      const sec = document.getElementById("daysec-" + r.day);
      if (sec) sec.className = "day" + (!r.feasible ? " bad" : (r.shortEst ? " low" : ""));
      const bn = document.getElementById("banner-" + r.day);
      if (bn) bn.innerHTML = statusHtml(r);
    }
    persist();
  }

  window.cmInput = function (el) {
    const m = el.id.match(/^d(\\d)-(.+)-cm$/);
    if (m) {
      const d = Number(m[1]), planet = m[2];
      state.days[d] = state.days[d] || {};
      state.days[d][planet] = state.days[d][planet] || {};
      state.days[d][planet].cmPct = Number(el.value) || 0;
    }
    const label = el.nextElementSibling;
    if (label) label.textContent = el.value + "%";
    updateResults();
  };

  window.deployInput = function (el) {
    state.deployPct = Number(el.value) || 0;
    document.getElementById("deploy-val").textContent = state.deployPct + "%";
    updateResults();
  };

  window.setGoal = function (name, value) {
    const m = name.match(/^d(\\d)-(.+)$/);
    if (m) {
      const d = Number(m[1]), planet = m[2];
      state.days[d] = state.days[d] || {};
      state.days[d][planet] = state.days[d][planet] || {};
      state.days[d][planet].goal = value;
    }
    lastFocusId = name;
    render();
  };

  window.setPlatoon = function (name, value) {
    const m = name.match(/^d(\\d)-(.+)-plats$/);
    if (m) {
      const d = Number(m[1]), planet = m[2];
      state.days[d] = state.days[d] || {};
      state.days[d][planet] = state.days[d][planet] || {};
      state.days[d][planet].platoons = Number(value) || 0;
    }
    lastFocusId = name;
    render();
  };

  window.setVal = function (id, value) {
    if (id === "unlock-zeffo") state.unlockZeffo = !!value;
    else if (id === "unlock-mandalore") state.unlockMandalore = !!value;
    lastFocusId = id;
    render();
  };

  window.selectPlan = function () {
    localStorage.setItem(LS_CURRENT, document.getElementById("plan-select").value);
    loadPlan(document.getElementById("plan-select").value);
    lastFocusId = null;
    render();
  };

  window.toggleCompact = function (el) {
    compact = !!el.checked;
    try { localStorage.setItem(LS_COMPACT, compact ? "1" : "0"); } catch (e) { /* ignore */ }
    render();
  };

  window.openNewPlan = function () {
    document.getElementById("np-name").value = "";
    document.getElementById("np-name").placeholder = "e.g. " + planName() + " (copy)";
    document.querySelector('input[name="np-type"][value="dup"]').checked = true;
    document.getElementById("np-overlay").style.display = "flex";
    document.getElementById("np-name").focus();
  };

  window.closeNewPlan = function () {
    document.getElementById("np-overlay").style.display = "none";
  };

  window.createNewPlan = function () {
    const name = document.getElementById("np-name").value.trim();
    if (!name) { document.getElementById("np-name").focus(); return; }
    const dup = document.querySelector('input[name="np-type"]:checked').value === "dup";
    const prevPlan = dup ? (loadPlans()[planName()] || {}) : {};
    const plan = dup
      ? {
          deployPct: state.deployPct,
          unlockZeffo: state.unlockZeffo,
          unlockMandalore: state.unlockMandalore,
          days: JSON.parse(JSON.stringify(state.days)),
          ...(prevPlan.fills ? { fills: JSON.parse(JSON.stringify(prevPlan.fills)) } : {}),
        }
      : { deployPct: 100, unlockZeffo: false, unlockMandalore: false, days: {} };
    const plans = loadPlans();
    plans[name] = plan;
    localStorage.setItem(LS_KEY, JSON.stringify(plans));
    localStorage.setItem(LS_CURRENT, name);
    state.deployPct = plan.deployPct;
    state.unlockZeffo = plan.unlockZeffo;
    state.unlockMandalore = plan.unlockMandalore;
    state.days = plan.days;
    closeNewPlan();
    lastFocusId = null;
    render();
  };

  // ---------- Optimizer ----------
  const CHAIN_IDS = ["light", "dark", "neutral"];
  const SP_CHAIN_IDX = { zeffo: 0, mandalore: 2 };
  const LS_OPT_EST = "roteCalcOptEst";
  const LS_OPT_PLANET = "roteCalcOptPlanet";
  const LS_OPT_PLATS = "roteCalcOptPlats";
  const LS_OPT_MODE = "roteCalcOptMode";
  const LEVEL_EST_DEFAULT = { 1: 30, 2: 20, 3: 10, 4: 5, 5: 0, 6: 0 };
  let optEst = {};
  let optEstPlanet = {};
  let optPlats = {};
  let optMode = "level";
  let optUnlock = { zeffo: false, mandalore: false };
  let optDeployPct = 100;
  let optResult = null;

  function phaseGroups() {
    const by = {};
    for (const ch of data.chains) for (const p of ch.planets) (by[p.phase] = by[p.phase] || []).push(p);
    for (const sp of data.specials) if (sp.planet) (by[sp.planet.phase] = by[sp.planet.phase] || []).push(sp.planet);
    return Object.keys(by).map(function (ph) {
      const planets = by[ph].slice().sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
      return { phase: Number(ph), relic: planets[0].relicReq, planets: planets };
    }).sort((a, b) => a.phase - b.phase);
  }

  function loadOptEst() {
    try { const p = JSON.parse(localStorage.getItem(LS_OPT_EST)); if (p && typeof p === "object") return p; } catch (e) { /* ignore */ }
    return null;
  }

  function saveOptEst() {
    try { localStorage.setItem(LS_OPT_EST, JSON.stringify(optEst)); } catch (e) { /* storage unavailable */ }
  }

  function loadOptPlanet() {
    try { const p = JSON.parse(localStorage.getItem(LS_OPT_PLANET)); if (p && typeof p === "object") return p; } catch (e) { /* ignore */ }
    return null;
  }

  function saveOptPlanet() {
    try { localStorage.setItem(LS_OPT_PLANET, JSON.stringify(optEstPlanet)); } catch (e) { /* storage unavailable */ }
  }

  function loadOptMode() {
    try { return localStorage.getItem(LS_OPT_MODE) || "level"; } catch (e) { return "level"; }
  }

  function saveOptMode() {
    try { localStorage.setItem(LS_OPT_MODE, optMode); } catch (e) { /* storage unavailable */ }
  }

  function estOf(est, key) { const v = est[key]; return v === undefined ? 100 : v; }
  function platOf(cap, ph) { const v = cap[ph]; return v === undefined ? 6 : v; }

  function loadOptPlats() {
    try { const p = JSON.parse(localStorage.getItem(LS_OPT_PLATS)); if (p && typeof p === "object") return p; } catch (e) { /* ignore */ }
    return null;
  }

  function saveOptPlats() {
    try { localStorage.setItem(LS_OPT_PLATS, JSON.stringify(optPlats)); } catch (e) { /* storage unavailable */ }
  }

  function starsOf(s) {
    return s.cs.light + s.cs.dark + s.cs.neutral + s.cs.zeffo + s.cs.mandalore;
  }

  function accessiblePlanets(st, unlockZ, unlockM) {
    const acc = [];
    for (let i = 0; i < CHAIN_IDS.length; i++) {
      const p = data.chains[i].planets[st.idx[i]];
      if (p) acc.push({ p: p, chain: i, special: null });
    }
    for (const sp of data.specials) {
      const unlocked = sp.id === "zeffo" ? unlockZ : unlockM;
      const done = sp.id === "zeffo" ? st.z : st.m;
      if (sp.planet && unlocked && !done && st.idx[SP_CHAIN_IDX[sp.id]] >= sp.triggerIndex) {
        acc.push({ p: sp.planet, chain: SP_CHAIN_IDX[sp.id], special: sp.id });
      }
    }
    return acc;
  }

  function optimizerStep(st, day, acc, gmap, est, platCap, deployPct) {
    let minTP = 0, maxTP = 0, platoon = 0, estCM = 0, totalCM = 0;
    for (const a of acc) {
      const p = a.p, g = gmap[p.name];
      totalCM += p.cmMax;
      if (g === 0) {
        maxTP += (p.thresholds[0] - 1);
      } else {
        const th = p.thresholds[g - 1] || 0;
        minTP += th; maxTP += th;
        const plats = Math.max(0, Math.min(p.platoonsTotal, platOf(platCap, p.phase)) - (st.plats[p.name] || 0));
        platoon += plats * p.platoonReward;
      }
      estCM += estOf(est, p.name) / 100 * p.cmMax;
    }
    const income = st.bank + deployPct / 100 * data.guildGp + platoon + estCM;
    if (income < minTP) return null;
    const capacity = Math.max(0, maxTP - minTP);
    const carry = clamp(income - minTP, 0, capacity);
    const ns = {
      idx: st.idx.slice(), z: st.z, m: st.m, bank: carry,
      cs: Object.assign({}, st.cs),
      plats: Object.assign({}, st.plats),
      days: st.days.slice(),
    };
    const acts = {};
    for (const a of acc) {
      const g = gmap[a.p.name];
      const plats = g >= 1 ? Math.max(0, Math.min(a.p.platoonsTotal, platOf(platCap, a.p.phase)) - (st.plats[a.p.name] || 0)) : 0;
      acts[a.p.name] = { goal: g, plats: plats, cmPct: estOf(est, a.p.name), special: !!a.special };
      if (g >= 1) {
        if (a.special) {
          if (a.special === "zeffo") ns.z = true; else ns.m = true;
          if (g === 3) ns.cs[a.special] += 1;
          ns.plats[a.p.name] = (st.plats[a.p.name] || 0) + plats;
        } else {
          ns.idx[a.chain] += 1;
          ns.cs[CHAIN_IDS[a.chain]] += g;
          ns.plats[a.p.name] = (st.plats[a.p.name] || 0) + plats;
        }
      }
    }
    ns.days.push({ day: day, acts: acts });
    return ns;
  }

  function optimizerPrio(s, unlockZ, unlockM) {
    let v = starsOf(s);
    if (unlockZ && s.z) v += 1000;
    if (unlockM && s.m) v += 1000;
    if (unlockZ && s.idx[0] >= 2) v += 2;
    if (unlockM && s.idx[2] >= 3) v += 2;
    return [v, s.bank];
  }

  window.optimizePlan = function (est, unlockZ, unlockM, deployPct, platCap) {
    const maxBeam = 90;
    platCap = platCap || {};
    const start = {
      idx: [0, 0, 0], z: false, m: false, bank: 0,
      cs: { light: 0, dark: 0, neutral: 0, zeffo: 0, mandalore: 0 },
      plats: {}, days: [],
    };
    let beam = [start];
    for (let day = 1; day <= 6; day++) {
      const cand = [];
      for (const st of beam) {
        const acc = accessiblePlanets(st, unlockZ, unlockM);
        if (!acc.length) { cand.push(st); continue; }
        const goalOpts = acc.map(a => a.special ? [0, 3] : [0, 1, 2, 3]);
        const idx = new Array(acc.length).fill(0);
        const total = goalOpts.reduce((n, o) => n * o.length, 1);
        for (let t = 0; t < total; t++) {
          const gmap = {};
          for (let i = 0; i < acc.length; i++) gmap[acc[i].p.name] = goalOpts[i][idx[i]];
          const ns = optimizerStep(st, day, acc, gmap, est, platCap, deployPct);
          if (ns) cand.push(ns);
          for (let i = acc.length - 1; i >= 0; i--) {
            if (++idx[i] < goalOpts[i].length) break;
            idx[i] = 0;
          }
        }
      }
      const by = {};
      for (const s of cand) {
        const key = s.idx[0] + "," + s.idx[1] + "," + s.idx[2] + "," + (s.z ? 1 : 0) + "," + (s.m ? 1 : 0);
        const e = by[key];
        if (!e || starsOf(s) > starsOf(e) || (starsOf(s) === starsOf(e) && s.bank > e.bank)) by[key] = s;
      }
      const vals = Object.values(by);
      vals.sort((a, b) => {
        const pa = optimizerPrio(a, unlockZ, unlockM), pb = optimizerPrio(b, unlockZ, unlockM);
        for (let i = 0; i < pa.length; i++) if (pa[i] !== pb[i]) return pb[i] - pa[i];
        return 0;
      });
      beam = vals.slice(0, maxBeam);
    }
    const req = [];
    if (unlockZ) req.push("zeffo");
    if (unlockM) req.push("mandalore");
    let best, unmet = [];
    const valid = beam.filter(s => req.every(r => r === "zeffo" ? s.z : s.m));
    if (valid.length) {
      best = valid[0];
    } else {
      best = beam[0];
      for (const r of req) if (!(r === "zeffo" ? best.z : best.m)) unmet.push(r);
    }
    return { stars: starsOf(best), cs: Object.assign({}, best.cs), days: best.days, unmet: unmet };
  };

  function levelDefault(ph) { return LEVEL_EST_DEFAULT[ph] !== undefined ? LEVEL_EST_DEFAULT[ph] : 100; }

  function seedOptEst() {
    const saved = loadOptEst();
    if (saved) { optEst = saved; return; }
    optEst = {};
    for (const g of phaseGroups()) optEst[g.phase] = levelDefault(g.phase);
  }

  function seedOptPlats() {
    const saved = loadOptPlats();
    if (saved) { optPlats = saved; return; }
    optPlats = {};
    for (const g of phaseGroups()) optPlats[g.phase] = 6;
  }

  function seedOptEstPlanet() {
    const saved = loadOptPlanet();
    if (saved) { optEstPlanet = saved; return; }
    optEstPlanet = {};
    for (const g of phaseGroups()) {
      for (const p of g.planets) optEstPlanet[p.name] = levelDefault(g.phase);
    }
  }

  function platSelectHtml(phase) {
    const pv = optPlats[phase] !== undefined ? optPlats[phase] : 6;
    let sel = '<select data-phase="' + phase + '" onchange="optPlatInput(this)" title="Platoons expected per planet in this phase (filled on the day it is starred)">';
    for (let n = 0; n <= 6; n++) sel += '<option value="' + n + '"' + (n === pv ? " selected" : "") + ">P" + n + "</option>";
    return sel + "</select>";
  }

  function levelEstHtml() {
    let html = "";
    for (const g of phaseGroups()) {
      const v = optEst[g.phase] !== undefined ? optEst[g.phase] : 100;
      html += '<div class="est-row"><span class="nm">Phase ' + g.phase + ' &middot; R' + g.relic + '</span>' +
        '<input type="range" min="0" max="100" step="1" value="' + v + '" data-phase="' + g.phase +
        '" oninput="optEstInput(this)">' +
        '<span class="pv">' + v + "%</span>" +
        platSelectHtml(g.phase) +
        '<span class="planets">' + g.planets.map(p => esc(p.name)).join(", ") + "</span></div>";
    }
    return html;
  }

  function planetEstHtml() {
    let html = "";
    for (const g of phaseGroups()) {
      html += '<div class="est-group"><div class="est-phase">Phase ' + g.phase + ' &middot; R' + g.relic +
        " " + platSelectHtml(g.phase) + "</div>";
      const ordered = g.planets.slice().sort((a, b) => nameOrderKey(a.name) - nameOrderKey(b.name));
      for (const p of ordered) {
        const v = optEstPlanet[p.name] !== undefined ? optEstPlanet[p.name] : 100;
        html += '<div class="est-planet"><span class="nm2">' + esc(p.name) + '</span>' +
          '<input type="range" min="0" max="100" step="1" value="' + v + '" data-planet="' + esc(p.name) +
          '" oninput="optEstInput(this)">' +
          '<span class="pv">' + v + "%</span></div>";
      }
      html += "</div>";
    }
    return html;
  }

  function optEstHtml() {
    const mode = optMode === "planet" ? "planet" : "level";
    const modeHtml = '<div class="opt-mode">' +
      '<label class="' + (mode === "level" ? "on" : "") + '"><input type="radio" name="optmode" value="level"' +
      (mode === "level" ? " checked" : "") + ' onchange="optSetMode(this.value)"> By level</label>' +
      '<label class="' + (mode === "planet" ? "on" : "") + '"><input type="radio" name="optmode" value="planet"' +
      (mode === "planet" ? " checked" : "") + ' onchange="optSetMode(this.value)"> By planet</label></div>';
    return modeHtml + (mode === "planet" ? planetEstHtml() : levelEstHtml());
  }

  function nameOrderKey(name) {
    for (const sp of data.specials) if (sp.planet && sp.planet.name === name) return PLANET_ORDER[sp.id] ?? 9;
    for (const ch of data.chains) for (const p of ch.planets) if (p.name === name) return PLANET_ORDER[ch.id] ?? 9;
    return 9;
  }

  function renderOptResult(res) {
    let html = '<div class="opt-line">Best plan: <b>' + res.stars + ' stars</b> &nbsp;·&nbsp; Light ' + res.cs.light +
      " · Dark " + res.cs.dark + " · Neutral " + res.cs.neutral + " · Zeffo " + res.cs.zeffo +
      " · Mandalore " + res.cs.mandalore + "</div>";
    for (const d of res.days) {
      const parts = Object.keys(d.acts).sort((a, b) => nameOrderKey(a) - nameOrderKey(b)).map(function (nm) {
        const a = d.acts[nm];
        if (a.goal >= 1) {
          const icon = a.special ? "&#9733;" : "&#9733;".repeat(a.goal);
          return esc(nm) + " " + icon;
        }
        return esc(nm) + ' <span class="muted">preload</span>';
      });
      html += '<div class="dayline">Day ' + d.day + ": " + parts.join(" &middot; ") + "</div>";
    }
    if (res.unmet.length) {
      html += '<div class="opt-warn">&#9888; Cannot max: ' + esc(res.unmet.join(", ")) +
        " at these estimates / deploy.</div>";
    }
    document.getElementById("opt-result").innerHTML = html;
    document.getElementById("opt-result").style.display = "block";
    document.getElementById("opt-apply").disabled = false;
  }

  window.openOpt = function () {
    optMode = loadOptMode();
    seedOptEst();
    seedOptEstPlanet();
    seedOptPlats();
    optUnlock.zeffo = state.unlockZeffo;
    optUnlock.mandalore = state.unlockMandalore;
    optDeployPct = state.deployPct;
    optResult = null;
    document.getElementById("opt-est").innerHTML = optEstHtml();
    document.getElementById("opt-unlock-zeffo").checked = optUnlock.zeffo;
    document.getElementById("opt-unlock-mandalore").checked = optUnlock.mandalore;
    document.getElementById("opt-deploy").value = optDeployPct;
    document.getElementById("opt-deploy-val").textContent = optDeployPct + "%";
    document.getElementById("opt-gp").textContent = fmt(data.guildGp);
    document.getElementById("opt-result").style.display = "none";
    document.getElementById("opt-apply").disabled = true;
    document.getElementById("opt-overlay").style.display = "flex";
  };

  window.closeOpt = function () {
    document.getElementById("opt-overlay").style.display = "none";
  };

  window.optEstInput = function (el) {
    if (el.dataset.planet !== undefined) {
      optEstPlanet[el.dataset.planet] = Number(el.value) || 0;
      saveOptPlanet();
    } else {
      optEst[Number(el.dataset.phase)] = Number(el.value) || 0;
      saveOptEst();
    }
    el.parentElement.querySelector(".pv").textContent = el.value + "%";
  };

  window.optSetMode = function (mode) {
    optMode = mode === "planet" ? "planet" : "level";
    saveOptMode();
    document.getElementById("opt-est").innerHTML = optEstHtml();
  };

  window.optPlatInput = function (el) {
    const ph = Number(el.dataset.phase);
    optPlats[ph] = Number(el.value) || 0;
    saveOptPlats();
  };

  window.optSetUnlock = function (id, val) {
    optUnlock[id] = !!val;
  };

  window.optSetDeploy = function (el) {
    optDeployPct = Number(el.value) || 0;
    document.getElementById("opt-deploy-val").textContent = optDeployPct + "%";
  };

  window.optResetEst = function () {
    for (const g of phaseGroups()) {
      optEst[g.phase] = levelDefault(g.phase);
      for (const p of g.planets) optEstPlanet[p.name] = levelDefault(g.phase);
    }
    saveOptEst();
    saveOptPlanet();
    document.getElementById("opt-est").innerHTML = optEstHtml();
  };

  window.runOpt = function () {
    let est;
    if (optMode === "planet") {
      est = optEstPlanet;
    } else {
      est = {};
      for (const g of phaseGroups()) for (const p of g.planets) est[p.name] = optEst[g.phase] !== undefined ? optEst[g.phase] : 100;
    }
    optResult = window.optimizePlan(est, optUnlock.zeffo, optUnlock.mandalore, optDeployPct, optPlats);
    renderOptResult(optResult);
  };

  window.applyOpt = function () {
    if (!optResult) return;
    state.deployPct = optDeployPct;
    state.unlockZeffo = !!optUnlock.zeffo;
    state.unlockMandalore = !!optUnlock.mandalore;
    state.days = {};
    for (let d = 1; d <= 6; d++) state.days[d] = {};
    for (const dayRec of optResult.days) {
      for (const nm in dayRec.acts) {
        const a = dayRec.acts[nm];
        state.days[dayRec.day][nm] = { goal: String(a.goal), platoons: a.plats, cmPct: a.cmPct };
      }
    }
    closeOpt();
    lastFocusId = null;
    render();
  };

  compact = loadCompact();
  if (!loadSharedPlan()) loadPlan(planName());
  render();
  loadGuildPlan();
})();
</script>
</body>
</html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("guild_id", nargs="?", default=DEFAULT_GUILD)
    parser.add_argument("--tb", default=TB_ID, help="TB id for the ROTE doc (default t05D)")
    parser.add_argument("--outdir", type=Path, default=data_root())
    args = parser.parse_args(argv)

    outdir = args.outdir
    data = build_data(outdir, args.guild_id, args.tb)
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = Environment(autoescape=False).from_string(HTML_TEMPLATE).render(
        guild_name=data["guildName"],
        guild_gp=data["guildGp"],
        data_json=data_json,
    )
    outpath = outdir / "guilds" / f"{args.guild_id}.calculator.html"
    outpath.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(outpath, html)
    print(f"wrote {outpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
