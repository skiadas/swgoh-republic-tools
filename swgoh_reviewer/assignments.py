#!/usr/bin/env python3
"""Assignments by member — read-only roster page for a guild's platoon plan.

For each guild member, lists all of their platoon assignments across all days,
from the plan's `fills` (the same per-guild localStorage plan objects the
planner uses). Summary table: member, total fills, per-day counts (across all
planets); each row expands to the day-by-day detail (`Day · Planet · P:pos ·
unit`). Per-(member, planet, day) groups over the 10-unit cap are flagged.

The page is a lighter sibling of the planner: `build_data(light=True)` drops
the per-member unit maps and slot combat/GL tags the interactive page needs,
so this page only resolves slot indexes to unit names.

Usage:
    python rote_assignments.py
    python rote_assignments.py NW4t0-dBRcG8n-PVhykpKg
"""

import argparse
import json
import sys
from pathlib import Path

from jinja2 import Environment

from swgoh_reviewer.config import data_root
from swgoh_reviewer.io import atomic_write_text
from swgoh_reviewer.platoons import DEFAULT_GUILD, TB_ID, build_data

MAX_UNITS = 10


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Assignments by member — {{ guild_name }}</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; background: #fafafa; color: #222; }
  header { background: #1c2541; color: #fff; padding: 12px 20px; }
  header h1 { margin: 0; font-size: 18px; }
  header .sub { font-size: 12px; opacity: .9; margin-top: 4px; }
  main { padding: 16px 20px; max-width: 900px; margin: 0 auto; }
  .controls { margin-bottom: 12px; }
  .controls label { margin-right: 14px; }
  .controls button { margin-right: 6px; }
  .notice { border: 1px solid #bdbdbd; background: #eceff1; border-radius: 6px; padding: 10px 14px; margin: 8px 0; font-size: 13px; }
  table.rost { border-collapse: collapse; width: 100%; font-size: 13px; background: #fff; border: 1px solid #ddd; }
  table.rost th, table.rost td { border: 1px solid #e2e6ee; padding: 5px 8px; text-align: left; }
  table.rost th { background: #f0f2f5; text-align: right; }
  table.rost th:first-child { text-align: center; }
  table.rost td.tot { font-weight: 700; text-align: right; }
  table.rost td.c { text-align: right; }
  table.rost td.muted, table.rost td.muted { color: #bbb; text-align: right; }
  table.rost tr.mrow { cursor: pointer; }
  table.rost tr.mrow:hover { background: #f4f7fb; }
  table.rost .car { text-align: center; color: #889; font-size: 10px; width: 18px; }
  table.rost .mname b { font-weight: 700; }
  table.rost .mdet { background: #f7f9fc; }
  table.rost .mdet > td { padding: 10px 14px; }
  .dhead { font-weight: 700; margin: 8px 0 3px; font-size: 12px; }
  .dhead:first-child { margin-top: 0; }
  .pline { font-size: 12px; margin: 2px 0 2px 14px; }
  .pline.over { color: #b71c1c; }
  .warn-tag { background: #f9a825; color: #fff; border-radius: 8px; padding: 0 6px; font-size: 10px; margin-left: 6px; }
  .copy-btn { border: none; background: none; color: #4a6fa5; cursor: pointer; font-size: 11px; padding: 0 4px; text-decoration: underline; }
  .copy-btn:hover { color: #1c2541; }
  .muted { color: #888; }
</style>
</head>
<body>
<header>
  <h1>Assignments by member — {{ guild_name }}</h1>
  <div class="sub">
    plan <select id="plan-select" onchange="selectPlan()"></select>
    &nbsp;<a href="./platoons" style="color:#9db3e0">open planner</a>
    &nbsp;<a href="./calc" style="color:#9db3e0">open calculator</a>
  </div>
</header>
<main>
  <div class="controls">
    <label>Search: <input id="search" placeholder="name or ally code" oninput="searchInput(this)"></label>
    <button onclick="toggleAll(true)">Expand all</button>
    <button onclick="toggleAll(false)">Collapse all</button>
    <span class="muted" id="summary-line"></span>
  </div>
  <div class="muted" id="plan-meta" style="margin-bottom:8px"></div>
  <div id="roster"></div>
</main>
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
  const MEMBER_MAP = {};
  const PLANET_MAP = {};
  for (const m of data.members) MEMBER_MAP[String(m.ac)] = m;
  for (const p of data.planets) PLANET_MAP[p.name] = p;
  const state = { fills: {}, search: "", guildPlan: null, usingGuild: false };
  const GUILD_VALUE = "__guild__";
  const MAX = {{ MAX_UNITS }};

  function esc(s) { return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
  function planName() { return localStorage.getItem(LS_CURRENT) || "Default"; }
  function loadPlans() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {}; } catch (e) { return {}; } }
  function loadPlan(name) {
    const saved = loadPlans()[name] || {};
    state.fills = saved.fills || {};
  }
  function loadGuild() {
    if (typeof fetch !== "function") return;
    fetch("/g/" + guildId + "/plan")
      .then(r => r.json())
      .then(d => {
        if (!d || !d.plan) return;
        state.guildPlan = d.plan;
        state.usingGuild = true;
        state.fills = (d.plan.payload && d.plan.payload.fills) || {};
        render();
      })
      .catch(() => {});
  }
  function planControlsHtml() {
    let html = '<select id="plan-select" onchange="selectPlan()">';
    if (state.guildPlan) {
      html += '<option value="' + GUILD_VALUE + '"' + (state.usingGuild ? " selected" : "") + ">Guild: " + esc(state.guildPlan.name) + "</option>";
    }
    const plans = loadPlans();
    const names = Object.keys(plans).length ? Object.keys(plans) : ["Default"];
    for (const n of names) {
      html += '<option value="' + esc(n) + '"' + (!state.usingGuild && n === planName() ? " selected" : "") + ">" + esc(n) + "</option>";
    }
    return html + "</select>";
  }
  window.selectPlan = function () {
    const sel = document.getElementById("plan-select");
    if (!sel) return;
    if (sel.value === GUILD_VALUE) {
      state.usingGuild = true;
      if (state.guildPlan) state.fills = (state.guildPlan.payload && state.guildPlan.payload.fills) || {};
      render();
      return;
    }
    state.usingGuild = false;
    try { localStorage.setItem(LS_CURRENT, sel.value); } catch (e) { /* ignore */ }
    loadPlan(sel.value);
    render();
  };

  function buildRoster() {
    const per = {};
    for (const pn in state.fills) {
      const planet = PLANET_MAP[pn];
      if (!planet) continue;
      const byDay = state.fills[pn] || {};
      for (const d in byDay) {
        const day = Number(d);
        for (const k in byDay[d]) {
          const ac = byDay[d][k];
          const slot = Number(k);
          const platoon = Math.floor(slot / 15), pos = slot % 15;
          const sl = planet.platoons[platoon].slots[pos];
          const r = per[ac] = per[ac] || { ac: ac, total: 0, days: {}, groups: {}, list: [] };
          r.total++;
          r.days[day] = (r.days[day] || 0) + 1;
          const gkey = pn + "\\u0000" + day;
          r.groups[gkey] = (r.groups[gkey] || 0) + 1;
          r.list.push({ day: day, planet: pn, platoon: platoon + 1, pos: pos, unit: sl.n });
        }
      }
    }
    for (const m of data.members) {
      const ac = String(m.ac);
      if (!per[ac]) per[ac] = { ac: ac, total: 0, days: {}, groups: {}, list: [] };
      per[ac].name = m.name;
    }
    return Object.values(per);
  }
  function detailHtml(r) {
    if (!r.list.length) return '<span class="muted">No assignments.</span>';
    const byDay = {};
    for (const it of r.list) (byDay[it.day] = byDay[it.day] || []).push(it);
    let html = "";
    for (const d of Object.keys(byDay).sort((a, b) => a - b)) {
      const byPlanet = {};
      for (const it of byDay[d]) (byPlanet[it.planet] = byPlanet[it.planet] || []).push(it);
      html += '<div class="dhead">Day ' + d + ' <span class="muted">(' + byDay[d].length + ")</span></div>";
      for (const pn in byPlanet) {
        const grp = byPlanet[pn];
        const over = grp.length > MAX;
        html += '<div class="pline' + (over ? " over" : "") + '"><b>' + esc(pn) + "</b>"
          + (over ? '<span class="warn-tag">>' + MAX + "</span>" : "") + ": "
          + grp.map(it => "Platoon " + it.platoon + " \u00b7 " + esc(it.unit)).join("; ") + "</div>";
      }
    }
    return html;
  }
  function memberMarkdown(r) {
    let md = "**" + r.name + "** (" + r.ac + ") \u2014 " + r.total + " assignments";
    if (!r.list.length) return md + "\\n\\nNo assignments.";
    const byDay = {};
    for (const it of r.list) (byDay[it.day] = byDay[it.day] || []).push(it);
    for (const d of Object.keys(byDay).sort((a, b) => a - b)) {
      md += "\\n\\n**Day " + d + "** (" + byDay[d].length + ")";
      for (const it of byDay[d]) md += "\\n- " + it.planet + " \u00b7 Platoon " + it.platoon + " \u00b7 " + it.unit;
    }
    return md;
  }
  window.copyMemberMarkdown = function (ac) {
    const r = buildRoster().find(x => String(x.ac) === String(ac));
    if (!r) return;
    const md = memberMarkdown(r);
    const btn = document.querySelector('.copy-btn[data-ac="' + ac + '"]');
    const flash = () => {
      if (!btn) return;
      const old = btn.textContent;
      btn.textContent = "Copied!";
      setTimeout(() => { btn.textContent = old; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(md).then(flash, () => { window.prompt("Copy:", md); });
    } else {
      window.prompt("Copy:", md);
    }
  };
  function renderRoster() {
    const rows = buildRoster();
    const q = state.search.toLowerCase();
    const filtered = rows.filter(r => !q || r.name.toLowerCase().includes(q) || String(r.ac).includes(q));
    filtered.sort((a, b) => (b.total - a.total) || a.name.localeCompare(b.name));
    let html = '<table class="rost"><tr><th></th><th>Member</th><th>Total</th>';
    for (let d = 1; d <= 6; d++) html += "<th>D" + d + "</th>";
    html += "</tr>";
    for (const r of filtered) {
      html += '<tr class="mrow" data-ac="' + esc(r.ac) + '">'
        + '<td class="car">' + (r.total ? "\u25b8" : "") + "</td>"
        + '<td class="mname"><b>' + esc(r.name) + '</b> <span class="muted">' + esc(String(r.ac)) + "</span>"
        + ' <button type="button" class="copy-btn" data-ac="' + esc(r.ac) + '" title="Copy Markdown for this member">copy</button></td>'
        + '<td class="tot">' + r.total + "</td>";
      for (let d = 1; d <= 6; d++) {
        const n = r.days[d] || 0;
        html += '<td class="' + (n ? "c" : "muted") + '">' + n + "</td>";
      }
      html += "</tr>";
      html += '<tr class="mdet" data-ac="' + esc(r.ac) + '" style="display:none"><td colspan="8">' + detailHtml(r) + "</td></tr>";
    }
    html += "</table>";
    document.getElementById("roster").innerHTML = html;
    const assigned = rows.filter(r => r.total > 0).length;
    const totalFills = rows.reduce((s, r) => s + r.total, 0);
    document.getElementById("summary-line").textContent = assigned + " of " + rows.length + " members assigned \u00b7 " + totalFills + " fills";
  }
  function render() {
    const sel = document.getElementById("plan-select");
    if (sel) sel.outerHTML = planControlsHtml();
    const meta = document.getElementById("plan-meta");
    if (meta) {
      if (state.usingGuild && state.guildPlan) {
        const at = String(state.guildPlan.updatedAt || "").slice(0, 16).replace("T", " ");
        meta.textContent = "Guild plan \u201c" + state.guildPlan.name + "\u201d \u00b7 updated by "
          + (state.guildPlan.ownerName || "admin") + (at ? " \u00b7 " + at : "");
      } else {
        meta.textContent = "Local plan \u201c" + planName() + "\u201d (this browser)";
      }
    }
    const hasFills = Object.keys(state.fills).some(pn => Object.keys(state.fills[pn] || {}).length);
    if (!hasFills) {
      document.getElementById("roster").innerHTML =
        '<div class="notice">No assignments in the current plan — open the <a href="./platoons">planner</a> to create them (or import a plan file there).</div>';
      document.getElementById("summary-line").textContent = "";
      return;
    }
    renderRoster();
  }
  document.addEventListener("click", function (ev) {
    const copy = ev.target.closest(".copy-btn");
    if (copy) {
      window.copyMemberMarkdown(copy.dataset.ac);
      ev.preventDefault();
      return;
    }
    const row = ev.target.closest(".mrow");
    if (!row) return;
    const ac = row.dataset.ac;
    const det = document.querySelector('.mdet[data-ac="' + ac + '"]');
    if (det) det.style.display = det.style.display === "none" ? "" : "none";
  });
  window.toggleAll = function (show) {
    for (const det of document.querySelectorAll(".mdet")) det.style.display = show ? "" : "none";
  };
  window.searchInput = function (el) { state.search = el.value; renderRoster(); };

  loadPlan(planName());
  render();
  loadGuild();
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

    data = build_data(outdir, args.guild_id, light=True)
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = Environment(autoescape=False).from_string(HTML_TEMPLATE).render(
        guild_name=data["guildName"],
        data_json=data_json,
        MAX_UNITS=MAX_UNITS,
    )
    outpath = outdir / "guilds" / f"{args.guild_id}.assignments.html"
    outpath.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(outpath, html)
    print(f"wrote {outpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
