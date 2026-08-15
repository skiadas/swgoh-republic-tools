import { test } from "node:test";
import assert from "node:assert/strict";
import { GUILD, assertDataReady, runUv, loadPage, pageData, dataFile } from "./helpers.mjs";

test("squad dashboard loads and renders the matrix (rows = players+2)", () => {
  assertDataReady();
  runUv("squad_report.py", GUILD);
  runUv("render_report.py", GUILD);
  const dom = loadPage(dataFile("guilds", `${GUILD}.squads.html`), `http://x.test/g/${GUILD}/report`);
  const w = dom.window;
  const data = pageData(dom);
  assert.ok(data.report, "page should expose DATA.report");
  const players = new Set(data.report.bySquad.flatMap((s) => (s.results || []).map((r) => String(r.allyCode))));
  const table = w.document.querySelector("#m-table table");
  assert.ok(table, "matrix table should render (Matrix is the default tab)");
  assert.equal(table.querySelectorAll("thead tr").length, 2);
  assert.equal(table.querySelectorAll("tr").length, players.size + 2);
  dom.window.close();
});
