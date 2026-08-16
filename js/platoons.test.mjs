import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM, VirtualConsole } from "jsdom";
import { GUILD, assertDataReady, runUv, pageData, dataFile } from "./helpers.mjs";

function platoonsDom(seedPlan) {
  assertDataReady();
  runUv("rote_calc.py", GUILD); // ensure rote data caches are present via the tool chain
  runUv("rote_platoons.py", GUILD);
  const html = readFileSync(dataFile("guilds", `${GUILD}.platoons.html`), "utf8");
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push(e.message));
  const dom = new JSDOM(html, {
    url: `http://x.test/g/${GUILD}/platoons`,
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

function planetOf(w, name) {
  return [...w.document.querySelectorAll(".planet")].find((p) => p.querySelector(".pname").textContent === name);
}
function cells(w, planet, slot) {
  return planet.querySelector(`[data-slot="${slot}"]`).closest(".cell");
}
function openPicker(w, planetEl, slot) {
  const chip = planetEl.querySelector(`.chip[data-slot="${slot}"]`);
  chip.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  return chip;
}
function pickerRows(w) {
  return [...w.document.querySelectorAll(".pick-row:not(.clear)")];
}
function memberNameOf(w, ac) {
  return pageData({ window: w }).members.find((m) => String(m.ac) === String(ac))?.name || String(ac);
}

const D1 = { days: { "1": { Coruscant: { goal: "1", platoons: 6, cmPct: 50 } } }, fills: {} };

test("platoon planner loads and renders day tabs + planets from the star plan", () => {
  const seed = {
    days: {
      "1": { Coruscant: { goal: "1", platoons: 6, cmPct: 50 } },
      "2": { Bracca: { goal: "0", platoons: 2, cmPct: 10 } },
      "3": { Zeffo: { goal: "0", platoons: 1, cmPct: 5 } },
    },
    fills: {},
  };
  const { dom, errors } = platoonsDom(seed);
  const w = dom.window;
  assert.deepEqual(errors, []);
  assert.equal([...w.document.querySelectorAll(".tab")].length, 6);
  const names = () => [...w.document.querySelectorAll(".planet .pname")].map((n) => n.textContent);
  assert.deepEqual(names(), ["Coruscant"], "day 1 shows only the plan's day-1 planets");
  assert.ok(!names().includes("Zeffo"));
  w.setDay(2);
  assert.deepEqual(names(), ["Bracca"], "day 2 shows only Bracca, not day-1 Coruscant");
  w.setDay(3);
  assert.deepEqual(names(), ["Zeffo"]);
  w.setDay(4);
  assert.equal(names().length, 0, "unplanned day shows nothing");
  assert.match(w.document.getElementById("days").textContent, /No planets planned for day 4/);
  dom.window.close();
});

test("planets within a day follow dark/neutral/light/specials order", () => {
  const seed = {
    days: {
      "3": {
        Zeffo: { goal: "0", platoons: 1, cmPct: 5 },
        Dathomir: { goal: "1", platoons: 4, cmPct: 40 },
        Kashyyyk: { goal: "1", platoons: 4, cmPct: 40 },
        Tatooine: { goal: "0", platoons: 2, cmPct: 10 },
      },
    },
    fills: {},
  };
  const { dom } = platoonsDom(seed);
  const w = dom.window;
  w.setDay(3);
  const names = [...w.document.querySelectorAll(".planet .pname")].map((n) => n.textContent);
  assert.deepEqual(names, ["Dathomir", "Tatooine", "Kashyyyk", "Zeffo"], "dark, neutral, light, special");
  dom.window.close();
});

test("assigning a fill persists it and marks the cell", () => {
  const { dom } = platoonsDom(D1);
  const w = dom.window;
  const chip = openPicker(w, planetOf(w, "Coruscant"), 0);
  const ac = pickerRows(w)[0].dataset.ac;
  w.assign(chip.dataset.pn, Number(chip.dataset.slot), chip.dataset.day, ac);
  const cell = cells(w, planetOf(w, "Coruscant"), 0);
  assert.ok(cell.classList.contains("cur"), "assigned-today cell class");
  const saved = JSON.parse(w.localStorage.getItem(`roteCalcPlans:${GUILD}`)).T;
  assert.equal(saved.fills.Coruscant["1"]["0"], ac, "fill persisted");
  dom.window.close();
});

test("covered slot from a prior day shows coverage and can be reassigned", () => {
  const seed = {
    days: {
      "1": { Coruscant: { goal: "0", platoons: 2, cmPct: 10 } },
      "2": { Coruscant: { goal: "1", platoons: 4, cmPct: 30 } },
    },
    fills: {},
  };
  const { dom } = platoonsDom(seed);
  const w = dom.window;
  w.setDay(1);
  openPicker(w, planetOf(w, "Coruscant"), 0);
  const acs = pickerRows(w).map((r) => r.dataset.ac);
  if (acs.length < 2) { dom.window.close(); return; } // data-dependent: need 2 eligible for slot 0
  const [acA, acB] = acs;
  w.assign("Coruscant", 0, "1", acA);
  w.setDay(2);
  let cell = cells(w, planetOf(w, "Coruscant"), 0);
  assert.ok(cell.classList.contains("cov"), "prior-day coverage on day 2");
  assert.equal(cell.querySelector(".chip .lbl").textContent, memberNameOf(w, acA));
  assert.equal(cell.querySelector(".chip").dataset.day, "2");
  w.assign("Coruscant", 0, "2", acB);
  cell = cells(w, planetOf(w, "Coruscant"), 0);
  assert.ok(cell.classList.contains("cur"), "reassigned today");
  const saved = JSON.parse(w.localStorage.getItem(`roteCalcPlans:${GUILD}`)).T;
  assert.equal(saved.fills.Coruscant["1"]["0"], acA, "prior-day fill kept");
  assert.equal(saved.fills.Coruscant["2"]["0"], acB, "new day fill added");
  dom.window.close();
});

test("completing a platoon before the planned star day warns", () => {
  const ac = "111111111";
  const seed = {
    days: {
      "1": { Coruscant: { goal: "0", platoons: 1, cmPct: 10 } },
      "3": { Coruscant: { goal: "0", platoons: 1, cmPct: 10 } },
      "5": { Coruscant: { goal: "3", platoons: 6, cmPct: 50 } },
    },
    fills: { Coruscant: { "1": Object.fromEntries([...Array(14)].map((_, i) => [String(i), ac])) } },
  };
  const { dom } = platoonsDom(seed);
  const w = dom.window;
  w.setDay(3);
  openPicker(w, planetOf(w, "Coruscant"), 14);
  const pickAc = pickerRows(w)[0].dataset.ac;
  w.assign("Coruscant", 14, "3", pickAc);
  const cell = cells(w, planetOf(w, "Coruscant"), 14);
  assert.ok(cell.classList.contains("warn"), "early-completion cell flagged");
  assert.match(cell.title, /planned to star day 5/);
  assert.match(planetOf(w, "Coruscant").querySelector(".platoon h4").textContent, /15\/15/);
  assert.match(w.document.querySelector(".warnbox").textContent, /planned to star day 5/);
  dom.window.close();
});

test("unit-once-per-day conflict across planets is flagged and dims the picker", () => {
  const { dom } = platoonsDom(D1);
  const w = dom.window;
  const cor = pageData(dom).planets.find((p) => p.name === "Coruscant");
  const slotsByUnit = {};
  cor.platoons.forEach((pl, pi) => pl.slots.forEach((s, si) => {
    (slotsByUnit[s.b] = slotsByUnit[s.b] || []).push(pi * 15 + si);
  }));
  const unit = Object.keys(slotsByUnit).find((k) => slotsByUnit[k].length >= 2);
  if (!unit) { dom.window.close(); return; } // data-dependent: no repeated unit on Coruscant
  const [slotA, slotB] = slotsByUnit[unit];
  openPicker(w, planetOf(w, "Coruscant"), slotA);
  const ac = pickerRows(w)[0].dataset.ac;
  w.assign("Coruscant", slotA, "1", ac);
  w.assign("Coruscant", slotB, "1", ac);
  const cell = cells(w, planetOf(w, "Coruscant"), slotB);
  assert.ok(cell.classList.contains("warn"), "duplicate-unit cell flagged");
  assert.match(cell.title, /already places/);
  openPicker(w, planetOf(w, "Coruscant"), slotB);
  const row = pickerRows(w).find((r) => r.dataset.ac === ac);
  assert.ok(row && row.classList.contains("dim"), "duplicate option dimmed in the picker");
  dom.window.close();
});

test("picker lists only eligible members and shows the eligible count", () => {
  const { dom } = platoonsDom(D1);
  const w = dom.window;
  const cor = pageData(dom).planets.find((p) => p.name === "Coruscant");
  const sl = cor.platoons[0].slots[0];
  const expected = pageData(dom).members
    .filter((m) => {
      const u = m.u[sl.b];
      return u && (sl.c === 2 ? u[1] >= 7 : u[0] >= 5);
    })
    .map((m) => String(m.ac));
  const cell = cells(w, planetOf(w, "Coruscant"), 0);
  assert.equal(cell.querySelector(".u .n").textContent, String(expected.length), "eligible count badge");
  openPicker(w, planetOf(w, "Coruscant"), 0);
  const rows = pickerRows(w);
  assert.equal(rows.length, expected.length, "only eligible members listed");
  assert.deepEqual(new Set(rows.map((r) => r.dataset.ac)), new Set(expected), "same member set");
  for (const r of rows) {
    assert.match(r.querySelector(".lvl").textContent, /^R\d+$|^\d+★$/, "level shown");
  }
  dom.window.close();
});

test("export payload round-trips through import", () => {
  const summary = JSON.parse(readFileSync(dataFile("guilds", `${GUILD}.summary.json`), "utf8"));
  const acs = summary.members.map((m) => String(m.allyCode));
  const [acA, acB] = [acs[0], acs[1] || acs[0]];
  const seed = {
    days: { "1": { Coruscant: { goal: "1", platoons: 4, cmPct: 50 } } },
    fills: { Coruscant: { "1": { "0": acA, "30": acB } } },
  };
  const { dom } = platoonsDom(seed);
  const w = dom.window;
  const payload = w.exportPayload();
  assert.equal(payload.format, "swgoh-plan");
  assert.equal(payload.days["1"].Coruscant.goal, "1");
  const f = payload.fills.Coruscant["1"];
  assert.ok(f.some((e) => e[1] === acA), "export carries member allycodes");
  assert.ok(f.every((e) => e.length === 3 && /^\d+:\d+$/.test(e[2])), "export slots are platoon:pos");
  // wipe local state, then import the payload back
  w.localStorage.removeItem(`roteCalcPlans:${GUILD}`);
  w.localStorage.setItem(`roteCalcCurrent:${GUILD}`, "Default");
  w.importPlan(payload);
  const saved = JSON.parse(w.localStorage.getItem(`roteCalcPlans:${GUILD}`)).T;
  assert.equal(saved.days["1"].Coruscant.goal, "1");
  assert.equal(saved.fills.Coruscant["1"]["0"], acA);
  assert.equal(saved.fills.Coruscant["1"]["30"], acB);
  dom.window.close();
});

test("import flags unknown members and skips unknown slots", () => {
  const summary = JSON.parse(readFileSync(dataFile("guilds", `${GUILD}.summary.json`), "utf8"));
  const ac = String(summary.members[0].allyCode);
  const payload = {
    format: "swgoh-plan", v: 1, g: GUILD, name: "Foreign",
    days: {},
    fills: {
      Coruscant: {
        "1": [
          ["Jedi Consular", "999999999", "1:0"],  // unknown member
          ["Bossk", ac, "999:999"],               // slot does not exist
          ["Chewbacca", ac, "1:0"],               // valid
        ],
      },
    },
  };
  const { dom } = platoonsDom();
  const w = dom.window;
  w.importPlan(payload);
  assert.match(w.document.getElementById("notice").textContent, /Unknown members .*999999999/);
  assert.match(w.document.getElementById("notice").textContent, /Skipped 1 fill/);
  const saved = JSON.parse(w.localStorage.getItem(`roteCalcPlans:${GUILD}`)).Foreign;
  assert.equal(saved.fills.Coruscant["1"]["0"], ac, "valid fill imported");
  assert.equal(Object.keys(saved.fills.Coruscant["1"]).length, 1, "unknown member + bad slot skipped");
  dom.window.close();
});

// ---- auto-generation ----

function platoonCounts(w, pn, d) {
  const plan = JSON.parse(w.localStorage.getItem(`roteCalcPlans:${GUILD}`)).T;
  const byPlanet = plan.fills[pn] || {};
  const counts = Array(6).fill(0);
  for (const dd in byPlanet) {
    if (Number(dd) > d) continue;
    for (const k in byPlanet[dd]) counts[Math.floor(Number(k) / 15)]++;
  }
  return counts;
}

test("genPick orders members by strategy", () => {
  const { dom } = platoonsDom();
  const w = dom.window;
  const cands = [
    { ac: "a", level: 9, dayTotal: 0, name: "A" },
    { ac: "b", level: 7, dayTotal: 0, name: "B" },
    { ac: "c", level: 9, dayTotal: 3, name: "C" },
  ];
  assert.equal(w.genPick(cands, "strongest").ac, "a", "strongest first, fewest-load tiebreak");
  assert.equal(w.genPick(cands, "weakest").ac, "b", "weakest qualifying first");
  assert.equal(w.genPick(cands, "minimize").ac, "a", "fewest today, strongest tiebreak");
  dom.window.close();
});

test("mode full completes every platoon even before the star day", () => {
  const seed = {
    days: { "1": { Coruscant: { goal: "0", platoons: 0, cmPct: 10 } }, "5": { Coruscant: { goal: "3", platoons: 6, cmPct: 50 } } },
    fills: {},
  };
  const { dom } = platoonsDom(seed);
  const w = dom.window;
  const added = w.generateAssignments({ mode: "planet", day: 1, planet: "Coruscant" }, "strongest", "full");
  assert.equal(added, 90, "fills all 90 slots");
  assert.deepEqual(platoonCounts(w, "Coruscant", 1), [15, 15, 15, 15, 15, 15], "every platoon 15/15 on a preload day");
  dom.window.close();
});

test("mode plan preloads to 14/15 and completes per the plan across days", () => {
  const seed = {
    days: { "1": { Coruscant: { goal: "0", platoons: 0, cmPct: 10 } }, "5": { Coruscant: { goal: "3", platoons: 6, cmPct: 50 } } },
    fills: {},
  };
  const { dom } = platoonsDom(seed);
  const w = dom.window;
  w.generateAssignments({ mode: "planet", day: 1, planet: "Coruscant" }, "strongest", "plan");
  assert.deepEqual(platoonCounts(w, "Coruscant", 1), [14, 14, 14, 14, 14, 14], "preload day: nothing completed");
  w.generateAssignments({ mode: "planet", day: 5, planet: "Coruscant" }, "strongest", "plan");
  assert.deepEqual(platoonCounts(w, "Coruscant", 5), [15, 15, 15, 15, 15, 15], "star day completes them");
  assert.equal(Object.keys((JSON.parse(w.localStorage.getItem(`roteCalcPlans:${GUILD}`)).T.fills.Coruscant || {})["5"] || {}).length, 6, "only the open slots filled on day 5");
  dom.window.close();
});

test("mode plan honors the plan's platoon count (2 of 6 complete)", () => {
  const seed = { days: { "1": { Coruscant: { goal: "0", platoons: 2, cmPct: 10 } } }, fills: {} };
  const { dom } = platoonsDom(seed);
  const w = dom.window;
  w.generateAssignments({ mode: "planet", day: 1, planet: "Coruscant" }, "strongest", "plan");
  assert.deepEqual(platoonCounts(w, "Coruscant", 1), [15, 15, 14, 14, 14, 14], "2 complete + 4 preloaded");
  dom.window.close();
});

test("mode plan leaves a Galactic Legend unassigned when available", () => {
  const seed = { days: { "2": { Bracca: { goal: "0", platoons: 0, cmPct: 10 } } }, fills: {} };
  const { dom } = platoonsDom(seed);
  const w = dom.window;
  const bracca = pageData(dom).planets.find((p) => p.name === "Bracca");
  w.generateAssignments({ mode: "planet", day: 2, planet: "Bracca" }, "strongest", "plan");
  const plan = JSON.parse(w.localStorage.getItem(`roteCalcPlans:${GUILD}`)).T;
  const fills = plan.fills.Bracca["2"] || {};
  for (let pidx = 0; pidx < 6; pidx++) {
    const uncovered = [];
    for (let s = pidx * 15; s < pidx * 15 + 15; s++) if (!fills[s]) uncovered.push(s);
    assert.equal(uncovered.length, 1, "each platoon preloaded to 14/15");
    const sl = bracca.platoons[pidx].slots[uncovered[0] % 15];
    const hasGl = bracca.platoons[pidx].slots.some((s) => s.gl);
    if (hasGl) assert.equal(sl.gl, 1, `P${pidx + 1} left a GL open`);
  }
  dom.window.close();
});

test("generation never assigns a unit twice to one member on a day", () => {
  const seed = {
    days: { "1": { Coruscant: { goal: "1", platoons: 6, cmPct: 50 }, Mustafar: { goal: "1", platoons: 6, cmPct: 50 } } },
    fills: {},
  };
  const { dom } = platoonsDom(seed);
  const w = dom.window;
  const data = pageData(dom);
  w.generateAssignments({ mode: "day", day: 1 }, "strongest", "full");
  const plan = JSON.parse(w.localStorage.getItem(`roteCalcPlans:${GUILD}`)).T;
  const seen = new Map(); // ac -> Set(baseId)
  for (const pn in plan.fills) {
    const byDay = plan.fills[pn]["1"] || {};
    for (const k in byDay) {
      const ac = byDay[k];
      const sl = data.planets.find((p) => p.name === pn).platoons[Math.floor(Number(k) / 15)].slots[Number(k) % 15];
      if (!seen.has(ac)) seen.set(ac, new Set());
      assert.ok(!seen.get(ac).has(sl.b), `member ${ac} placed ${sl.b} twice on day 1`);
      seen.get(ac).add(sl.b);
    }
  }
  dom.window.close();
});

test("day auto button opens the modal and generate fills the day", () => {
  const seed = { days: { "1": { Coruscant: { goal: "1", platoons: 6, cmPct: 50 } } }, fills: {} };
  const { dom } = platoonsDom(seed);
  const w = dom.window;
  const btn = w.document.querySelector(".gen-btn[data-mode=\"day\"]");
  assert.ok(btn, "day auto button rendered");
  btn.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const overlay = w.document.getElementById("gen-overlay");
  assert.ok(overlay.classList.contains("show"), "modal opened");
  assert.match(w.document.getElementById("gen-scope").textContent, /Day 1/);
  w.document.querySelector('input[name="gen-strategy"][value="minimize"]').checked = true;
  w.generateNow();
  assert.ok(!overlay.classList.contains("show"), "modal closed after generate");
  assert.ok(Object.keys((JSON.parse(w.localStorage.getItem(`roteCalcPlans:${GUILD}`)).T.fills.Coruscant || {})["1"] || {}).length > 0, "fills generated");
  dom.window.close();
});

test("clear all wipes fills but keeps the star plan", () => {
  const seed = { days: { "1": { Coruscant: { goal: "1", platoons: 6, cmPct: 50 } } }, fills: { Coruscant: { "1": { "0": "111111111" } } } };
  const { dom } = platoonsDom(seed);
  const w = dom.window;
  w.confirm = () => true;
  w.clearAll();
  const plan = JSON.parse(w.localStorage.getItem(`roteCalcPlans:${GUILD}`)).T;
  assert.deepEqual(plan.fills, {}, "fills wiped");
  assert.equal(plan.days["1"].Coruscant.goal, "1", "star plan kept");
  dom.window.close();
});
