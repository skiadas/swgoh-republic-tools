#!/usr/bin/env python3
"""Render the squad report as a single self-contained HTML page.

Reads only local files:
    data/guilds/<guildId>.squads.json     squad report (from squad_report.py)
    data/guilds/<guildId>.summary.json    guild summary (for player GP)

Emits data/guilds/<guildId>.squads.html with the data inlined as a JS
constant. No server, no CDN - open the file directly in a browser.

Views: Matrix, Squads, Players, Needs.

Usage:
    python render_report.py NW4t0-dBRcG8n-PVhykpKg
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment

from swgoh_reviewer.config import data_root
from swgoh_reviewer.io import atomic_write_text

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
  :root {
    --green: #2e7d32; --green-bg: #dcedc8;
    --amber: #b26a00; --amber-bg: #ffe0b2;
    --orange: #b26500; --orange-bg: #ffcc80;
    --red: #b71c1c; --red-bg: #ffcdd2;
    --bg: #fafafa; --panel: #ffffff; --border: #ddd;
    --muted: #666;
  }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    margin: 0; background: var(--bg); color: #222;
  }
  header {
    position: sticky; top: 0; background: #1c2541; color: #fff;
    padding: 10px 16px; z-index: 10; box-shadow: 0 1px 4px rgba(0,0,0,.3);
  }
  header h1 { margin: 0; font-size: 18px; }
  header .sub { font-size: 12px; opacity: .8; margin-top: 2px; }
  nav.tabs { display: flex; gap: 4px; padding: 8px 16px 0; background: var(--panel);
    border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 5; }
  nav.tabs button {
    border: 1px solid var(--border); border-bottom: none; background: #eee;
    padding: 7px 14px; cursor: pointer; font-size: 14px; border-radius: 6px 6px 0 0;
  }
  nav.tabs button.active { background: var(--panel); font-weight: 600; }
  main { padding: 16px; max-width: 1400px; margin: 0 auto; }
  .view { display: none; }
  .view.active { display: block; }
  table { border-collapse: collapse; background: var(--panel); width: 100%;
    font-size: 13px; border: 1px solid var(--border); }
  th, td { border: 1px solid var(--border); padding: 5px 7px; text-align: left;
    white-space: nowrap; }
  th { background: #f0f2f5; position: sticky; top: 0; }
  tr:nth-child(even) { background: #fafbfc; }
  .controls { margin-bottom: 10px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
  .controls label { font-size: 13px; }
  select, input { padding: 5px 8px; font-size: 13px; border: 1px solid var(--border); border-radius: 4px; }
  .cell { text-align: center; font-weight: 600; cursor: help; }
  .cell.g0 { background: var(--green-bg); color: var(--green); }
  .cell.g1 { background: var(--amber-bg); color: var(--amber); }
  .cell.g2 { background: var(--orange-bg); color: var(--orange); }
  .cell.g3 { background: var(--red-bg); color: var(--red); }
  .cell.na { background: #eee; color: #aaa; }
  .cell-sub { font-size: 10px; opacity: .8; font-weight: 600; margin-left: 2px; }
  .cell.cr-none { background: #eee; color: #aaa; }
  .cell.cr0 { background: #ffcdd2; color: #b71c1c; }
  .cell.cr1 { background: #ffccbc; color: #bf360c; }
  .cell.cr2 { background: #ffe0b2; color: #b26500; }
  .cell.cr3 { background: #fff59d; color: #827717; }
  .cell.cr4 { background: #dce775; color: #827717; }
  .cell.cr5 { background: #aed581; color: #33691e; }
  .cell.cr6 { background: #81c784; color: #1b5e20; }
  .cell.cr7 { background: #66bb6a; color: #1b5e20; }
  .cell.cr8 { background: #4caf50; color: #fff; }
  .cat-head { text-align: center; background: #e8eaf0 !important; font-size: 13px; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 11px;
    font-weight: 700; }
  .b-met { background: var(--green-bg); color: var(--green); }
  .b-upgrade { background: var(--amber-bg); color: var(--amber); }
  .b-missing { background: var(--red-bg); color: var(--red); }
  .b-complete { background: var(--green-bg); color: var(--green); }
  .b-gap { background: var(--amber-bg); color: var(--amber); }
  .muted { color: var(--muted); }
  .legend { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
  .legend span { display: inline-block; padding: 0 8px; margin-right: 4px; border-radius: 4px; }
  section.squad-block { margin-bottom: 24px; }
  section.squad-block h3 { margin: 4px 0 6px; }
  .empty { color: var(--muted); font-style: italic; padding: 20px; }
  .player-card { border: 1px solid var(--border); background: var(--panel);
    border-radius: 6px; padding: 12px; margin-bottom: 16px; }
  details { margin: 4px 0; }
  summary { cursor: pointer; font-weight: 600; }
  .need-list { margin: 2px 0 10px; }
  #mtip {
    position: fixed; z-index: 1000; display: none; pointer-events: none;
    background: #1e2430; color: #e8eaf0; border: 1px solid #3a4356;
    border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,.45);
    padding: 10px 12px; font-size: 12px; max-width: 360px; line-height: 1.45;
  }
  .tt-title { font-weight: 700; font-size: 13px; margin-bottom: 4px; }
  .tt-sub { font-weight: 400; color: #9aa5b8; font-size: 11px; }
  .tt-sect { margin-top: 8px; font-size: 10px; text-transform: uppercase;
    letter-spacing: .05em; color: #9aa5b8; border-bottom: 1px solid #3a4356; padding-bottom: 2px; }
  .tt-row { display: flex; align-items: baseline; gap: 6px; margin: 2px 0; }
  .tt-name { font-weight: 600; }
  .tt-relic { color: #c8d0e0; }
  .tt-need { color: #ffb74d; font-weight: 600; }
  .sym-met { color: #81c784; } .sym-upgrade { color: #ffb74d; } .sym-missing { color: #e57373; }
  .tt-improv { margin-top: 8px; border-top: 1px solid #3a4356; padding-top: 6px; }
  .tt-head { font-weight: 700; color: #ffb74d; font-size: 11px;
    text-transform: uppercase; letter-spacing: .04em; margin-bottom: 4px; }
  .tt-improv ul { margin: 0; padding-left: 16px; }
  .tt-improv li { margin: 2px 0; }
  .tt-improv.done .tt-head { color: #81c784; }
  .player-head { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
  .player-head .gp { margin-left: auto; font-size: 13px; }
  .cat-section { margin-bottom: 18px; }
  .cat-title { font-weight: 700; font-size: 14px; margin: 0 0 6px; display: flex; gap: 8px; align-items: baseline; }
  .cat-title .count { font-weight: 400; color: var(--muted); font-size: 12px; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .chip { display: inline-flex; align-items: center; gap: 4px; border-radius: 10px; padding: 2px 8px; font-size: 11px; white-space: nowrap; }
  .chip-met { background: var(--green-bg); color: var(--green); }
  .chip-upgrade { background: var(--amber-bg); color: var(--amber); }
  .chip-missing { background: var(--red-bg); color: var(--red); }
  .chip .chip-relic { font-weight: 700; }
  .pchip { display: inline-flex; align-items: baseline; gap: 4px; border-radius: 10px;
    background: #eef0f5; border: 1px solid var(--border); padding: 2px 8px; font-size: 11px; white-space: nowrap; }
  .pchip .pchip-code { color: var(--muted); font-size: 10px; }
  .squad-needs { margin-bottom: 14px; }
  .need-title { font-weight: 700; font-size: 13px; margin: 0 0 4px; display: flex; gap: 8px; align-items: baseline; }
  .need-title .count { font-weight: 400; color: var(--muted); font-size: 11px; }
  table.need-table { width: 100%; }
  table.need-table td { vertical-align: top; padding: 4px 8px; }
  td.need-action { white-space: nowrap; font-weight: 600; width: 1%; }
  .sq-tabs { display: flex; gap: 4px; overflow-x: auto; padding-bottom: 6px; margin-bottom: 10px; border-bottom: 1px solid var(--border); }
  .sq-tabs button { border: 1px solid var(--border); background: #f0f2f5; padding: 6px 12px; cursor: pointer; font-size: 12px; border-radius: 6px; white-space: nowrap; }
  .sq-tabs button.active { background: #1c2541; color: #fff; font-weight: 600; }
  .relic-blocked { color: #b26a00; font-weight: 700; }
  tr.row-ready td:first-child { border-left: 3px solid #66bb6a; }
  tr.row-progress td:first-child { border-left: 3px solid #ffb74d; }
  tr.row-none td:first-child { border-left: 3px solid #e57373; }
  .status-meta { color: var(--muted); font-size: 11px; }
</style>
</head>
<body>
<header>
  <h1>{{ guild_name }} &mdash; squad readiness</h1>
  <div class="sub">{{ guild_id }} &middot; generated {{ generated_at }}</div>
</header>
<nav class="tabs">
  <button data-view="matrix" class="active">Matrix</button>
  <button data-view="squads">Squads</button>
  <button data-view="players">Players</button>
  <button data-view="needs">Needs</button>
</nav>
<main>
  <div id="view-matrix" class="view active"></div>
  <div id="view-squads" class="view"></div>
  <div id="view-players" class="view"></div>
  <div id="view-needs" class="view"></div>
</main>
<script>
const DATA = {{ data_json }};
</script>
<script>
(function () {
  "use strict";
  const report = DATA.report;
  const guild = DATA.guild;
  const gpByAlly = Object.fromEntries((guild.members || []).map(m => [String(m.allyCode), m.galacticPower]));
  const gpInfoByAlly = Object.fromEntries((guild.members || []).map(m => [String(m.allyCode), m]));

  const squadGroups = [];
  const squadIndex = {}; // key "cat|squad" -> group
  (report.bySquad || []).forEach(s => {
    s.key = s.category + "|" + s.squad;
    s.resByAlly = Object.fromEntries(s.results.map(r => [String(r.allyCode), r]));
    squadGroups.push(s);
    squadIndex[s.key] = s;
  });

  const players = [];
  const seen = new Set();
  squadGroups.forEach(s => s.results.forEach(r => {
    if (seen.has(String(r.allyCode))) return;
    seen.add(String(r.allyCode));
    players.push({ allyCode: String(r.allyCode), name: r.name, gp: gpByAlly[String(r.allyCode)] });
  }));
  players.sort((a, b) => a.name.localeCompare(b.name));

  // category grouping for matrix columns
  const categories = [];
  report.bySquad.forEach(s => {
    let c = categories.find(x => x.name === s.category);
    if (!c) { c = { name: s.category, squads: [] }; categories.push(c); }
    c.squads.push(s);
  });

  const gapClass = g => g === 0 ? "g0" : g === 1 ? "g1" : g === 2 ? "g2" : "g3";
  const statusDot = s => s === "met" ? "\\u2713" : s === "upgrade" ? "\\u25b2" : "\\u2717";
  const CR_COLORS = ["cr0", "cr1", "cr2", "cr3", "cr4", "cr5", "cr6", "cr7", "cr8"];
  const relicLabel = u => (u.relicLevel === null || u.relicLevel === undefined) ? "missing" : "R" + u.relicLevel;
  const squadTitle = s => s.mode === "commonRelic"
    ? "common relic " + s.thresholds.map(t => "R" + t).join("/") + ", size " + s.size
    : "minRelic " + s.minRelic + ", size " + s.size;
  const commonCellClass = (v, s) => {
    if (v === null) return "cr-none";
    const i = s.thresholds.indexOf(v);
    return CR_COLORS[i] || "cr6";
  };

  function totalGap(playerCode) {
    let t = 0;
    squadGroups.forEach(s => {
      const r = s.resByAlly[playerCode];
      if (r && s.mode !== "commonRelic") t += (r.gap || 0);
    });
    return t;
  }

  function isComplete(playerCode) {
    return squadGroups.every(s => {
      const r = s.resByAlly[playerCode];
      if (!r) return false;
      if (s.mode === "commonRelic") return r.commonRelic !== null && r.nextThreshold === null;
      return !!r.complete;
    });
  }

  function esc(s) { return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

  function cellHtml(r, s) {
    if (!r) return '<td data-key="' + s.key + '" class="cell na">-</td>';
    if (s.mode === "commonRelic") {
      const label = r.commonRelic === null ? "-" : "R" + r.commonRelic;
      return '<td data-key="' + s.key + '" class="cell ' + commonCellClass(r.commonRelic, s) + '">' + label + "</td>";
    }
    if (r.complete) return '<td data-key="' + s.key + '" class="cell ' + gapClass(0) + '">\u2713</td>';
    const up = r.required.filter(u => u.status === "upgrade").length +
      r.poolChosen.filter(u => u.status === "upgrade").length;
    const miss = r.required.filter(u => u.status === "missing").length +
      r.poolChosen.filter(u => u.status === "missing").length;
    const sub = (up || miss) ? ' <span class="cell-sub">\u25b2' + up + "\u2717" + miss + "</span>" : "";
    return '<td data-key="' + s.key + '" class="cell ' + gapClass(r.gap) + '">' + r.gap + sub + "</td>";
  }

  // ---------- Matrix tooltip ----------
  const tipEl = () => {
    let el = document.getElementById("mtip");
    if (!el) { el = document.createElement("div"); el.id = "mtip"; document.body.appendChild(el); }
    return el;
  };

  function positionTip(x, y) {
    const el = tipEl();
    const r = el.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    let left = x + 14, top = y + 14;
    if (left + r.width > vw - 8) left = x - r.width - 14;
    if (top + r.height > vh - 8) top = y - r.height - 14;
    el.style.left = left + "px";
    el.style.top = top + "px";
  }

  function tipUnitRow(u, s) {
    const sym = u.status === "met" ? '<span class="sym-met">&#10003;</span>'
      : u.status === "upgrade" ? '<span class="sym-upgrade">&#9650;</span>'
      : '<span class="sym-missing">&#10007;</span>';
    const relic = (u.relicLevel === null || u.relicLevel === undefined)
      ? '<span class="sym-missing">missing</span>' : "R" + u.relicLevel;
    let need = "";
    if (s.mode !== "commonRelic" && u.status === "upgrade") {
      const target = (u.minRelic !== null && u.minRelic !== undefined) ? u.minRelic : s.minRelic;
      need = ' <span class="tt-need">&rarr; needs R' + target + "</span>";
    }
    return '<div class="tt-row">' + sym + ' <span class="tt-name">' + esc(u.name) +
      '</span> <span class="tt-relic">' + relic + "</span>" + need + "</div>";
  }

  function improveList(items, done) {
    if (items.length) {
      return '<div class="tt-improv' + (done ? " done" : "") + '"><div class="tt-head">' +
        (done ? "Complete" : "What to improve") + "</div><ul>" +
        items.map(i => "<li>" + esc(i) + "</li>").join("") + "</ul></div>";
    }
    return "";
  }

  function minTipHtml(r, s) {
    let html = '<div class="tt-title">' + esc(r.name) + ' <span class="tt-sub">' + esc(s.squad) + "</span></div>";
    const status = r.complete ? '<span class="sym-met">&#10003; ready</span>' : 'gap <b>' + r.gap + "</b> <span class=\\"status-meta\\">(2&times;missing + 1&times;below-relic)</span>";
    html += "<div>" + status + " &middot; min relic R" + s.minRelic + " &middot; size " + s.size + "</div>";
    html += '<div class="tt-sect">Required</div>';
    const needs = [];
    r.required.forEach(u => {
      html += tipUnitRow(u, s);
      if (u.status === "missing") needs.push("Acquire " + u.name + " (not owned)");
      else if (u.status === "upgrade") needs.push("Relic " + u.name + " to R" + ((u.minRelic != null) ? u.minRelic : s.minRelic));
    });
    if (r.poolCount > 0) {
      html += '<div class="tt-sect">Pool (need ' + r.poolCount + " of " + r.poolTotal + " owned)</div>";
      r.poolChosen.forEach(u => {
        html += tipUnitRow(u, s);
        if (u.status === "missing") needs.push("Acquire pool unit " + u.name);
        else if (u.status === "upgrade") needs.push("Relic " + u.name + " to R" + ((u.minRelic != null) ? u.minRelic : s.minRelic));
      });
    }
    return html + improveList(needs, r.complete);
  }

  function commonTipHtml(r, s) {
    const label = r.commonRelic === null ? '<span class="sym-missing">none</span>' : "<b>R" + r.commonRelic + "</b>";
    let html = '<div class="tt-title">' + esc(r.name) + ' <span class="tt-sub">' + esc(s.squad) + "</span></div>";
    html += "<div>common relic: " + label;
    if (r.nextThreshold !== null && r.nextThreshold !== undefined) html += " &middot; next: <b>R" + r.nextThreshold + "</b>";
    html += "</div>";
    html += '<div class="tt-sect">Required</div>';
    const needs = [];
    const next = r.nextThreshold;
    r.required.forEach(u => {
      if (u.relicLevel === null || u.relicLevel === undefined) {
        html += '<div class="tt-row"><span class="sym-missing">&#10007;</span> <span class="tt-name">' + esc(u.name) + '</span> <span class="sym-missing">missing</span></div>';
        needs.push("Acquire " + u.name + " (not owned)");
      } else {
        const below = next !== null && next !== undefined && u.relicLevel < next;
        const mark = below ? '<span class="sym-upgrade">&#9650;</span>' : '<span class="sym-met">&#10003;</span>';
        html += '<div class="tt-row">' + mark + ' <span class="tt-name">' + esc(u.name) + '</span> <span class="tt-relic">R' + u.relicLevel + "</span>" +
          (below ? ' <span class="tt-need">&rarr; needs R' + next + "</span>" : "") + "</div>";
        if (below) needs.push("Relic " + u.name + " to R" + next);
      }
    });
    if (r.poolCount > 0) {
      html += '<div class="tt-sect">Pool (need ' + r.poolCount + ")</div>";
      r.poolChosen.forEach(u => {
        html += tipUnitRow(u, s);
      });
    }
    const done = r.commonRelic !== null && next === null;
    return html + improveList(needs, done);
  }

  function wireMatrixTooltips() {
    const tbl = document.getElementById("m-table");
    const el = tipEl();
    tbl.onmouseover = e => {
      const td = e.target.closest("td.cell[data-key]");
      if (!td) { el.style.display = "none"; return; }
      const s = squadIndex[td.dataset.key];
      const code = td.closest("tr").dataset.ally;
      const r = s && s.resByAlly[code];
      if (!r) { el.style.display = "none"; return; }
      el.innerHTML = (s.mode === "commonRelic") ? commonTipHtml(r, s) : minTipHtml(r, s);
      el.style.display = "block";
      positionTip(e.clientX, e.clientY);
    };
    tbl.onmousemove = e => {
      if (el.style.display !== "none") positionTip(e.clientX, e.clientY);
    };
    tbl.onmouseleave = () => { el.style.display = "none"; };
  }

  // ---------- Matrix ----------
  function buildMatrixControls() {
    const view = document.getElementById("view-matrix");
    view.innerHTML = '<div class="controls">' +
      '<label>Sort <select id="m-sort">' +
      '<option value="gp" selected>GP</option><option value="name">Name</option><option value="gap">Total gap</option>' +
      '</select></label>' +
      '<label><input type="checkbox" id="m-hide"> hide complete players</label>' +
      '<label>Search <input id="m-search" type="search" placeholder="player name"></label>' +
      '</div><div class="legend">gap = 2&times;missing + 1&times;below-relic: ' +
      '<span class="cell g0">0</span><span class="cell g1">1</span>' +
      '<span class="cell g2">2</span><span class="cell g3">3+</span> ' +
      '<span class="muted">(cell shows gap and &#9650;below-relic / &#10007;missing)</span>' +
      ' &nbsp; common relic: <span class="cell cr-none">-</span>' +
      '<span class="cell cr0">low</span><span class="cell cr3">mid</span><span class="cell cr6">high</span>' +
      '</div><div id="m-table"></div>';
    document.getElementById("m-sort").onchange = renderMatrixBody;
    document.getElementById("m-hide").onchange = renderMatrixBody;
    document.getElementById("m-search").oninput = renderMatrixBody;
  }

  function renderMatrix() {
    if (!document.getElementById("m-sort")) buildMatrixControls();
    renderMatrixBody();
  }

  function renderMatrixBody() {
    const sort = document.getElementById("m-sort").value;
    const hide = document.getElementById("m-hide").checked;
    const q = document.getElementById("m-search").value.toLowerCase();

    let list = players.filter(p => !q || p.name.toLowerCase().includes(q));
    list = hide ? list.filter(p => !isComplete(p.allyCode)) : list;
    if (sort === "gp") list.sort((a, b) => (b.gp || 0) - (a.gp || 0));
    else if (sort === "gap") list.sort((a, b) => totalGap(b.allyCode) - totalGap(a.allyCode));
    else list.sort((a, b) => a.name.localeCompare(b.name));

    let html = '<table><thead><tr><th rowspan="2">Player</th><th rowspan="2">GP</th>';
    categories.forEach(c => { html += '<th class="cat-head" colspan="' + c.squads.length + '">' + esc(c.name) + "</th>"; });
    html += "</tr><tr>";
    categories.forEach(c => c.squads.forEach(s => { html += "<th title=\\"" + esc(squadTitle(s)) + "\\">" + esc(s.squad) + "</th>"; }));
    html += "</tr></thead><tbody>";
    list.forEach(p => {
      html += '<tr data-ally="' + esc(p.allyCode) + '"><td><b>' + esc(p.name) + '</b> <span class="muted">' + esc(p.allyCode) + "</span></td><td>" +
        (p.gp ? Number(p.gp).toLocaleString() : "-") + "</td>";
      categories.forEach(c => c.squads.forEach(s => { html += cellHtml(s.resByAlly[p.allyCode], s); }));
      html += "</tr>";
    });
    html += "</tbody></table>";
    document.getElementById("m-table").innerHTML = html;
    wireMatrixTooltips();
  }

  // ---------- Squads ----------
  function squadTableHtml(s) {
    const cols = s.results.length ? s.results[0].required.map(u => u.name) : [];
    let html = '<section class="squad-block"><h3>' + esc(s.category) + " / " + esc(s.squad) +
      ' <span class="muted">(' + squadTitle(s) + ")</span></h3>";
    html += '<table><thead><tr><th>Player</th><th>' + (s.mode === "commonRelic" ? "Common" : "Gap") + '</th>';
    cols.forEach(c => html += "<th>" + esc(c) + "</th>");
    html += "<th>Pool</th></tr></thead><tbody>";
    const rows = s.results.slice();
    if (s.mode === "commonRelic") {
      rows.sort((a, b) => (b.commonRelic ?? -1) - (a.commonRelic ?? -1));
    } else {
      rows.sort((a, b) => (a.complete === b.complete ? a.gap - b.gap : a.complete ? -1 : 1));
    }
    rows.forEach(r => {
      if (s.mode === "commonRelic") {
        const label = r.commonRelic === null ? "-" : "R" + r.commonRelic;
        const badgeCls = r.commonRelic === null ? "cr-none" : commonCellClass(r.commonRelic, s);
        const tip = "common relic: " + label +
          " \u00b7 next: " + (r.nextThreshold === null ? "-" : "R" + r.nextThreshold) +
          (r.bottlenecks.length ? "\\nblocked by: " + r.bottlenecks.join(", ") : "");
        html += '<tr><td><b>' + esc(r.name) + '</b> <span class="muted">' + esc(r.allyCode) + "</span></td>" +
          '<td><span class="badge ' + badgeCls + '" title="' + esc(tip) + '">' + label + "</span></td>";
        r.required.forEach(u => {
          if (u.relicLevel === null) html += '<td><span class="sym-missing">missing</span></td>';
          else {
            const below = r.nextThreshold !== null && u.relicLevel < r.nextThreshold;
            html += '<td>' + (below ? '<span class="relic-blocked">R' + u.relicLevel + "</span>" : "R" + u.relicLevel) + "</td>";
          }
        });
        const pool = r.poolChosen.map(u => u.name + " " + relicLabel(u)).join(", ");
        html += '<td title="' + esc(pool) + '">' + (pool || "-") + "</td></tr>";
      } else {
        html += '<tr><td><b>' + esc(r.name) + '</b> <span class="muted">' + esc(r.allyCode) + "</span></td>" +
          '<td><span class="badge ' + (r.complete ? "b-complete" : "b-gap") + '">' +
          (r.complete ? "ready" : r.gap) + "</span></td>";
        r.required.forEach(u => {
          html += '<td><span class="badge b-' + u.status + '">' + statusDot(u.status) + "</span> " +
            (u.status !== "missing" ? "R" + u.relicLevel : "-") + "</td>";
        });
        const pool = r.poolChosen.map(u => u.name + " " + statusDot(u.status) + (u.status !== "missing" ? "(R" + u.relicLevel + ")" : "")).join(", ");
        html += '<td title="' + esc(pool) + '">' + r.poolMet + "/" + r.poolCount + " met" +
          (r.poolUpgrade ? ' <span class="muted">+' + r.poolUpgrade + " up</span>" : "") +
          (r.poolMissing ? ' <span class="muted">-' + r.poolMissing + " miss</span>" : "") + "</td></tr>";
      }
    });
    html += "</tbody></table></section>";
    return html;
  }

  function renderSquads() {
    const view = document.getElementById("view-squads");
    if (!squadGroups.length) { view.innerHTML = '<div class="empty">No squads defined.</div>'; return; }
    let html = '<div class="sq-tabs">';
    squadGroups.forEach((s, i) => {
      html += '<button data-sq="' + i + '"' + (i === 0 ? ' class="active"' : "") + ">" +
        esc(s.category) + " &middot; " + esc(s.squad) + "</button>";
    });
    html += '</div><div id="sq-content">' + squadTableHtml(squadGroups[0]) + "</div>";
    view.innerHTML = html;
    view.querySelectorAll(".sq-tabs button").forEach(btn => {
      btn.onclick = () => {
        view.querySelectorAll(".sq-tabs button").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById("sq-content").innerHTML = squadTableHtml(squadGroups[Number(btn.dataset.sq)]);
      };
    });
  }

  // ---------- Players ----------
  function renderPlayers() {
    let html = '<div class="controls"><label>Player <select id="p-select"></select></label></div><div id="p-detail"></div>';
    document.getElementById("view-players").innerHTML = html;
    const sel = document.getElementById("p-select");
    players.forEach(p => {
      const opt = document.createElement("option");
      opt.value = p.allyCode; opt.textContent = p.name + " (" + p.allyCode + ")";
      sel.appendChild(opt);
    });
    sel.onchange = () => renderPlayerDetail(sel.value);
    renderPlayerDetail(sel.value);
  }

  function unitChip(u, s, next) {
    const missing = (u.relicLevel === null || u.relicLevel === undefined);
    let status = "met";
    if (missing) status = "missing";
    else if (s.mode === "commonRelic" && next !== null && next !== undefined && u.relicLevel < next) status = "upgrade";
    else if (s.mode !== "commonRelic" && u.status === "upgrade") status = "upgrade";
    const sym = status === "met" ? "\u2713" : status === "upgrade" ? "\u25b2" : "\u2717";
    const relic = missing ? "missing" : "R" + u.relicLevel;
    return '<span class="chip chip-' + status + '">' + sym + " " + esc(u.name) +
      ' <span class="chip-relic">' + relic + "</span></span>";
  }

  function squadStatusHtml(s, r) {
    if (s.mode === "commonRelic") {
      if (r.commonRelic === null) return '<span class="badge b-missing">none</span>';
      const label = "R" + r.commonRelic;
      if (r.nextThreshold === null) return '<span class="badge b-complete">' + label + "</span> <span class=\\"status-meta\\">max</span>";
      return '<span class="badge b-gap">' + label + "</span> <span class=\\"status-meta\\">&rarr; R" + r.nextThreshold + "</span>";
    }
    if (r.complete) return '<span class="badge b-complete">ready</span>';
    return '<span class="badge b-gap">gap ' + r.gap + "</span>";
  }

  function squadRowClass(s, r) {
    if (s.mode === "commonRelic") return r.commonRelic === null ? "row-none" : (r.nextThreshold === null ? "row-ready" : "row-progress");
    return r.complete ? "row-ready" : "row-progress";
  }

  function renderPlayerDetail(code) {
    const p = players.find(x => x.allyCode === code);
    const el = document.getElementById("p-detail");
    if (!p) { el.innerHTML = '<div class="empty">Select a player.</div>'; return; }
    const info = gpInfoByAlly[code] || {};

    let html = '<div class="player-card"><div class="player-head"><h3>' + esc(p.name) +
      ' <span class="muted">' + esc(p.allyCode) + "</span></h3>";
    html += '<span class="gp">GP <b>' + (p.gp ? Number(p.gp).toLocaleString() : "-") + "</b>";
    if (info.characterGalacticPower) html += ' <span class="status-meta">(char ' + Number(info.characterGalacticPower).toLocaleString() +
      " / ship " + Number(info.shipGalacticPower).toLocaleString() + ")</span>";
    html += "</span></div>";

    categories.forEach(cat => {
      const rows = cat.squads.map(s => ({ s, r: s.resByAlly[code] })).filter(x => x.r);
      if (!rows.length) return;
      html += '<section class="cat-section"><div class="cat-title">' + esc(cat.name) +
        ' <span class="count">' + rows.length + " squad" + (rows.length !== 1 ? "s" : "") + "</span></div>";
      html += "<table><thead><tr><th>Squad</th><th>Status</th><th>Required</th><th>Pool</th></tr></thead><tbody>";
      rows.forEach(({ s, r }) => {
        const next = r.nextThreshold !== undefined ? r.nextThreshold : null;
        const chips = r.required.map(u => unitChip(u, s, next)).join("");
        const poolChips = r.poolCount > 0
          ? r.poolChosen.map(u => unitChip(u, s, next)).join("")
          : '<span class="muted">-</span>';
        html += '<tr class="' + squadRowClass(s, r) + '"><td><b>' + esc(s.squad) + "</b></td>" +
          "<td>" + squadStatusHtml(s, r) + "</td>" +
          '<td><div class="chips">' + chips + "</div></td>" +
          '<td><div class="chips">' + poolChips + "</div></td></tr>";
      });
      html += "</tbody></table></section>";
    });
    html += "</div>";
    el.innerHTML = html;
  }

  // ---------- Needs ----------
  function pchip(name, code, title) {
    return '<span class="pchip" data-name="' + esc(String(name).toLowerCase()) + '"' +
      (title ? ' title="' + esc(title) + '"' : "") + ">" + esc(name) +
      ' <span class="pchip-code">(' + esc(code) + ")</span></span>";
  }

  function needsRows(s) {
    const rows = [];
    if (s.mode === "commonRelic") {
      const missing = s.results.filter(r => r.commonRelic === null);
      if (missing.length) rows.push({
        label: "Missing a required unit",
        players: missing.map(r => ({
          name: r.name, code: r.allyCode,
          title: "missing: " + r.required.filter(u => u.relicLevel === null).map(u => u.name).join(", ")
        }))
      });
      s.thresholds.forEach((t, i) => {
        if (i === 0) return;
        const near = s.results.filter(r => r.nextThreshold === t);
        if (!near.length) return;
        rows.push({
          label: "Need R" + t + " (next step)",
          players: near.map(r => ({
            name: r.name, code: r.allyCode,
            title: "at R" + (r.commonRelic === null ? "0" : r.commonRelic) +
              (r.bottlenecks.length ? " · blocked by " + r.bottlenecks.join(", ") : "")
          }))
        });
      });
      return rows;
    }
    const reqNames = s.results.length ? s.results[0].required.map(u => u.name) : [];
    reqNames.forEach((uname, i) => {
      const missing = s.results.filter(r => r.required[i].status === "missing");
      const below = s.results.filter(r => r.required[i].status === "upgrade");
      if (missing.length) rows.push({
        label: "Missing: " + uname,
        players: missing.map(r => ({ name: r.name, code: r.allyCode, title: "not owned" }))
      });
      if (below.length) rows.push({
        label: "Below relic " + s.minRelic + ": " + uname,
        players: below.map(r => ({
          name: r.name, code: r.allyCode,
          title: "R" + r.required[i].relicLevel + " \u2192 needs R" +
            (r.required[i].minRelic != null ? r.required[i].minRelic : s.minRelic)
        }))
      });
    });
    const short = s.results.filter(r => r.poolMet < r.poolCount);
    if (short.length) rows.push({
      label: "Pool short (need " + s.poolCount + ")",
      players: short.map(r => ({
        name: r.name, code: r.allyCode,
        title: "met " + r.poolMet + ", upgrade " + r.poolUpgrade + ", missing " + r.poolMissing
      }))
    });
    return rows;
  }

  function needsCount(s) {
    if (s.mode === "commonRelic") return s.results.filter(r => r.commonRelic === null || r.nextThreshold !== null).length;
    return s.results.filter(r => !r.complete).length;
  }

  function squadNeedsHtml(s, q) {
    const rows = needsRows(s)
      .map(row => ({ label: row.label, players: q ? row.players.filter(p => p.name.toLowerCase().includes(q)) : row.players }))
      .filter(row => row.players.length);
    if (!rows.length) {
      return '<div class="empty">No needs' + (q ? ' match "' + esc(q) + '"' : "") + ".</div>";
    }
    const n = needsCount(s);
    let html = '<div class="squad-needs"><div class="need-title">' + esc(s.squad) +
      ' <span class="status-meta">(' + squadTitle(s) + ")</span>" +
      ' <span class="count">' + n + " player" + (n !== 1 ? "s" : "") + " need work</span></div>";
    html += '<table class="need-table"><tbody>';
    rows.forEach(row => {
      html += '<tr><td class="need-action">' + esc(row.label) + "</td>" +
        '<td><div class="chips">' + row.players.map(p => pchip(p.name, p.code, p.title)).join("") + "</div></td></tr>";
    });
    html += "</tbody></table></div>";
    return html;
  }

  let needsActive = 0;

  function renderNeedsContent() {
    const q = (document.getElementById("n-search") ? document.getElementById("n-search").value : "").toLowerCase().trim();
    const el = document.getElementById("needs-content");
    if (el) el.innerHTML = squadNeedsHtml(squadGroups[needsActive] || squadGroups[0], q);
  }

  function renderNeeds() {
    const view = document.getElementById("view-needs");
    if (!squadGroups.length) {
      view.innerHTML = '<div class="empty">No squads defined.</div>';
      return;
    }
    needsActive = Math.min(needsActive, squadGroups.length - 1);
    let html = '<div class="controls"><label>Filter players <input id="n-search" type="search" placeholder="player name"></label></div>';
    html += '<div class="sq-tabs">';
    squadGroups.forEach((s, i) => {
      html += '<button data-sq="' + i + '"' + (i === needsActive ? ' class="active"' : "") + ">" +
        esc(s.category) + " &middot; " + esc(s.squad) + "</button>";
    });
    html += '</div><div id="needs-content"></div>';
    view.innerHTML = html;
    const inp = document.getElementById("n-search");
    if (inp) inp.oninput = renderNeedsContent;
    view.querySelectorAll(".sq-tabs button").forEach(btn => {
      btn.onclick = () => {
        view.querySelectorAll(".sq-tabs button").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        needsActive = Number(btn.dataset.sq);
        renderNeedsContent();
      };
    });
    renderNeedsContent();
  }

  // tabs
  document.querySelectorAll("nav.tabs button").forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll("nav.tabs button").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("view-" + btn.dataset.view).classList.add("active");
      if (btn.dataset.view === "matrix") renderMatrix();
      if (btn.dataset.view === "squads") renderSquads();
      if (btn.dataset.view === "players") renderPlayers();
      if (btn.dataset.view === "needs") renderNeeds();
    };
  });

  renderMatrix();
})();
</script>
</body>
</html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("guild_id")
    parser.add_argument("--outdir", type=Path, default=data_root(), help="data directory")
    args = parser.parse_args(argv)

    outdir = args.outdir
    report_path = outdir / "guilds" / f"{args.guild_id}.squads.json"
    summary_path = outdir / "guilds" / f"{args.guild_id}.summary.json"
    if not report_path.exists():
        print(f"no report at {report_path}; run squad_report.py first", file=sys.stderr)
        return 2

    report = json.loads(report_path.read_text())
    guild = {"guildId": report["guildId"], "guildName": report["guildName"], "members": []}
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        guild["members"] = [
            {
                "allyCode": m.get("allyCode"),
                "name": m.get("name"),
                "galacticPower": m.get("galacticPower"),
                "characterGalacticPower": m.get("characterGalacticPower"),
                "shipGalacticPower": m.get("shipGalacticPower"),
            }
            for m in summary.get("members", [])
        ]

    title = f"{report['guildName']} — squad readiness"
    data_js = json.dumps({"report": report, "guild": guild}, ensure_ascii=False).replace("</", "<\\/")
    html = Environment(autoescape=False).from_string(HTML_TEMPLATE).render(
        title=title,
        guild_name=report["guildName"],
        guild_id=report["guildId"],
        generated_at=report["generatedAt"],
        data_json=data_js,
    )

    outpath = outdir / "guilds" / f"{args.guild_id}.squads.html"
    atomic_write_text(outpath, html)
    print(f"wrote {outpath} ({outpath.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
