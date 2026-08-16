import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM, VirtualConsole } from "jsdom";
import { GUILD, assertDataReady, runUv, dataFile } from "./helpers.mjs";

function assignmentsDom(seedPlan) {
  assertDataReady();
  runUv("rote_assignments.py", GUILD);
  const html = readFileSync(dataFile("guilds", `${GUILD}.assignments.html`), "utf8");
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push(e.message));
  const dom = new JSDOM(html, {
    url: `http://x.test/g/${GUILD}/assignments`,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(w) {
      w.localStorage.setItem(`roteCalcPlans:${GUILD}`, JSON.stringify({ T: seedPlan || { days: {}, fills: {} } }));
      w.localStorage.setItem(`roteCalcCurrent:${GUILD}`, "T");
    },
  });
  return { dom, errors };
}

// seed uses real allycodes from the roster
function realAcs() {
  const summary = JSON.parse(readFileSync(dataFile("guilds", `${GUILD}.summary.json`), "utf8"));
  return summary.members.map((m) => String(m.allyCode));
}

test("assignments page loads without JS errors", () => {
  const { dom, errors } = assignmentsDom();
  assert.deepEqual(errors, []);
  dom.window.close();
});

test("empty plan shows the empty state, not a roster", () => {
  const { dom } = assignmentsDom();
  const w = dom.window;
  assert.match(w.document.getElementById("roster").textContent, /No assignments in the current plan/);
  assert.equal(w.document.querySelectorAll(".mrow").length, 0);
  dom.window.close();
});

test("lists every member and shows per-day totals matching the fills", () => {
  const [acA, acB] = realAcs();
  const seed = {
    days: {},
    fills: {
      Coruscant: {
        "1": { "0": acA, "1": acA, "2": acA, "30": acB },
        "3": { "5": acA },
      },
    },
  };
  const { dom } = assignmentsDom(seed);
  const w = dom.window;
  const summary = JSON.parse(readFileSync(dataFile("guilds", `${GUILD}.summary.json`), "utf8"));
  assert.equal(w.document.querySelectorAll(".mrow").length, summary.members.length, "all members listed");
  const rows = [...w.document.querySelectorAll(".mrow")];
  const byAc = (ac) => rows.find((r) => r.dataset.ac === ac);
  const a = byAc(acA);
  assert.equal(a.querySelector(".tot").textContent, "4", "total for A");
  const tds = [...a.querySelectorAll("td")];
  assert.equal(tds[3].textContent, "3", "day 1 count");
  assert.equal(tds[5].textContent, "1", "day 3 count");
  assert.equal(tds[4].textContent, "0", "day 2 count");
  assert.match(w.document.getElementById("summary-line").textContent, /2 of .* members assigned · 5 fills/);
  dom.window.close();
});

test("expanding a member shows day · planet · P:pos · unit", () => {
  const [acA] = realAcs();
  const seed = { days: {}, fills: { Coruscant: { "1": { "0": acA, "30": acA } } } };
  const { dom } = assignmentsDom(seed);
  const w = dom.window;
  const row = [...w.document.querySelectorAll(".mrow")].find((r) => r.dataset.ac === acA);
  row.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const det = w.document.querySelector(`.mdet[data-ac="${acA}"]`);
  assert.equal(det.style.display, "", "detail expanded");
  assert.match(det.textContent, /Day 1 \(2\)/);
  assert.match(det.textContent, /Coruscant/);
  assert.match(det.textContent, /Platoon 1 · General Skywalker/);
  assert.match(det.textContent, /Platoon 3 · Jedi Knight Luke Skywalker/);
  assert.ok(!/\bP\d+:\d+\b/.test(det.textContent), "no compact P:pos slot refs");
  assert.ok(det.querySelectorAll(".pline").length === 1, "one planet line");
  dom.window.close();
});

test("per-planet-day groups over 10 are flagged", () => {
  const [acA] = realAcs();
  const fills = { "1": {} };
  for (let s = 0; s < 11; s++) fills["1"][String(s)] = acA; // 11 on Coruscant day 1
  const seed = { days: {}, fills: { Coruscant: fills } };
  const { dom } = assignmentsDom(seed);
  const w = dom.window;
  const row = [...w.document.querySelectorAll(".mrow")].find((r) => r.dataset.ac === acA);
  row.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const det = w.document.querySelector(`.mdet[data-ac="${acA}"]`);
  const over = det.querySelector(".pline.over");
  assert.ok(over, "over-cap line flagged");
  assert.match(over.textContent, />10/);
  dom.window.close();
});

test("search filters the roster", () => {
  const [acA] = realAcs();
  const seed = { days: {}, fills: { Coruscant: { "1": { "0": acA } } } };
  const { dom } = assignmentsDom(seed);
  const w = dom.window;
  const total = w.document.querySelectorAll(".mrow").length;
  assert.ok(total > 0, "roster rendered");
  w.searchInput({ value: "zzzz-no-such-member" });
  assert.equal(w.document.querySelectorAll(".mrow").length, 0);
  w.searchInput({ value: "" });
  assert.equal(w.document.querySelectorAll(".mrow").length, total);
  dom.window.close();
});

test("switching plans re-renders", () => {
  const [acA] = realAcs();
  const seed = {
    P1: { days: {}, fills: { Coruscant: { "1": { "0": acA } } } },
    P2: { days: {}, fills: {} },
  };
  const dom = new JSDOM(readFileSync(dataFile("guilds", `${GUILD}.assignments.html`), "utf8"), {
    url: `http://x.test/g/${GUILD}/assignments`,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(w) {
      w.localStorage.setItem(`roteCalcPlans:${GUILD}`, JSON.stringify(seed));
      w.localStorage.setItem(`roteCalcCurrent:${GUILD}`, "P1");
    },
  });
  const w = dom.window;
  assert.ok(w.document.querySelectorAll(".mrow").length > 0, "P1 has assignments");
  w.selectPlan();
  const sel = w.document.getElementById("plan-select");
  sel.value = "P2";
  w.selectPlan();
  assert.match(w.document.getElementById("roster").textContent, /No assignments in the current plan/);
  dom.window.close();
});

test("every member row has a copy button and it copies Markdown", () => {
  const [acA] = realAcs();
  const seed = { days: {}, fills: { Coruscant: { "1": { "0": acA, "1": acA, "15": acA }, "3": { "5": acA } } } };
  const { dom } = assignmentsDom(seed);
  const w = dom.window;
  let copied = "";
  w.navigator.clipboard = { writeText: async (t) => { copied = t; } };
  assert.equal(w.document.querySelectorAll(".copy-btn").length, w.document.querySelectorAll(".mrow").length, "copy button on every row");
  const row = [...w.document.querySelectorAll(".mrow")].find((r) => r.dataset.ac === acA);
  row.querySelector(".copy-btn").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert.match(copied, new RegExp("\\*\\*" + row.querySelector(".mname b").textContent.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\*\\*"));
  assert.match(copied, /— 4 assignments/);
  assert.match(copied, /\*\*Day 1\*\* \(3\)/);
  assert.match(copied, /\*\*Day 3\*\* \(1\)/);
  assert.match(copied, /- Coruscant · Platoon 1 · General Skywalker/);
  assert.ok(!/\bP\d+:\d+\b/.test(copied), "no compact P:pos slot refs");
  dom.window.close();
});

test("copying does not toggle the member detail", () => {
  const [acA] = realAcs();
  const seed = { days: {}, fills: { Coruscant: { "1": { "0": acA } } } };
  const { dom } = assignmentsDom(seed);
  const w = dom.window;
  w.navigator.clipboard = { writeText: async () => {} };
  const row = [...w.document.querySelectorAll(".mrow")].find((r) => r.dataset.ac === acA);
  row.querySelector(".copy-btn").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const det = w.document.querySelector(`.mdet[data-ac="${acA}"]`);
  assert.equal(det.style.display, "none", "detail stays collapsed");
  dom.window.close();
});

test("zero-assignment member copies a zero-assignment Markdown", () => {
  const [acA] = realAcs();
  const seed = { days: {}, fills: { Coruscant: { "1": { "0": acA } } } };
  const { dom } = assignmentsDom(seed);
  const w = dom.window;
  let copied = "";
  w.navigator.clipboard = { writeText: async (t) => { copied = t; } };
  const row = [...w.document.querySelectorAll(".mrow")].find((r) => r.dataset.ac !== acA);
  row.querySelector(".copy-btn").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert.match(copied, /— 0 assignments/);
  assert.match(copied, /No assignments\./);
  dom.window.close();
});
