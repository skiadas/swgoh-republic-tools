import { test } from "node:test";
import assert from "node:assert/strict";
import { GUILD, assertDataReady, runUv, loadPage, pageData, dataFile } from "./helpers.mjs";

function calcDom() {
  assertDataReady();
  runUv("rote_calc.py", GUILD);
  return loadPage(dataFile("guilds", `${GUILD}.calculator.html`), `http://x.test/g/${GUILD}/calc`);
}

function allEst(w, pct) {
  const data = pageData({ window: w });
  const est = {};
  for (const ch of data.chains) for (const p of ch.planets) est[p.name] = pct;
  for (const sp of data.specials) est[sp.planet.name] = pct;
  return est;
}
const stars = (w, pct, uz, um) => w.optimizePlan(allEst(w, pct), uz, um, 100, 6).stars;

test("calculator page loads without JS errors", () => {
  const dom = calcDom();
  assert.ok(dom.window.optimizePlan, "optimizePlan should be exposed");
  dom.window.close();
});

test("optimizer sanity: 100% -> 47* (no unlocks) / 52* (both specials); 50% -> 43*; 30% -> 41*", () => {
  // Data-dependent: these track the current ROTE game data and can drift on
  // game updates (re-derive with a fresh t05D.json if they fail).
  const dom = calcDom();
  const w = dom.window;
  assert.equal(stars(w, 100, false, false), 47);
  assert.equal(stars(w, 100, true, true), 52);
  assert.equal(stars(w, 50, false, false), 43);
  assert.equal(stars(w, 30, false, false), 41);
  dom.window.close();
});

test("share URL embeds the plan and round-trips", () => {
  const dom = calcDom();
  const w = dom.window;
  const planet = pageData(dom).chains[0].planets[0].name;
  let copiedUrl = "";
  w.navigator.clipboard = { writeText: async (u) => { copiedUrl = u; } };
  w.setVal("unlock-zeffo", true);
  w.setGoal(`d1-${planet}`, "3");
  w.setPlatoon(`d1-${planet}-plats`, "6");
  w.sharePlan();
  assert.ok(copiedUrl, "sharePlan should copy a URL");
  const enc = new URL(copiedUrl).searchParams.get("plan");
  assert.ok(enc, "share URL should carry ?plan=");
  const bin = w.atob(enc.replace(/-/g, "+").replace(/_/g, "/"));
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  const decoded = JSON.parse(new TextDecoder().decode(bytes));
  assert.equal(decoded.unlockZeffo, true);
  assert.equal(decoded.days["1"][planet].goal, "3");
  assert.equal(decoded.days["1"][planet].platoons, 6);
  dom.window.close();
});
