import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { JSDOM } from "jsdom";

export const GUILD = "NW4t0-dBRcG8n-PVhykpKg";
export const DATA_ROOT = "data";

export function dataFile(...parts) {
  return join(DATA_ROOT, ...parts);
}

export function assertDataReady() {
  for (const p of [dataFile("rote", "t05D.json"), dataFile("guilds", `${GUILD}.summary.json`)]) {
    try {
      readFileSync(p);
    } catch {
      throw new Error(
        `missing ${p} — generate the pages first: \`uv run python fetch_guild.py <allycode>\` (or \`--guild-id ${GUILD}\`) and \`uv run python rote.py\``
      );
    }
  }
}

export function runUv(...args) {
  try {
    execFileSync("uv", ["run", "python", ...args], { stdio: "pipe" });
  } catch (e) {
    throw new Error(`\`uv run python ${args.join(" ")}\` failed:\n${e.stderr || e.message}`);
  }
}

export function loadPage(htmlPath, url) {
  const html = readFileSync(htmlPath, "utf8");
  return new JSDOM(html, {
    url,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(window) {
      // jsdom doesn't expose TextEncoder/TextDecoder (browsers do); the calc
      // page's share encoding uses them.
      window.TextEncoder = TextEncoder;
      window.TextDecoder = TextDecoder;
    },
  });
}

// `const DATA = {...}` is a global lexical binding, not a window property.
export function pageData(dom) {
  return dom.window.eval("DATA");
}
