#!/usr/bin/env python3
"""Job runner: regenerate reports/calculator from cache, or refresh a guild.

Wraps the CLI mains (via argv) and pipeline.refresh_guild so the web layer
drives the same code the CLI does. All jobs are serialized by a lock so a
manual action never collides with the nightly refresh.
"""

import os
import queue
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from swgoh_reviewer import calc, dashboard, pipeline, platoons, squads, tb
from swgoh_reviewer.config import data_root
from swgoh_reviewer.io import atomic_write_text

# The ROTE campaign the site plans around (internal id; never shown to users).
TB_ID = "t05D"


class JobError(RuntimeError):
    pass


class JobRunner:
    def __init__(self, db, outdir=None, max_rps=4.0):
        self.db = db
        self.outdir = Path(outdir or data_root())
        self.max_rps = max_rps
        # RLock so refresh_guild can call regen (which also takes the lock)
        # without deadlocking the worker thread.
        self._lock = threading.RLock()
        self._queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def enqueue(self, kind, guild_id, fn):
        """Run fn in the background.

        Logs a "running" job row now; the job fn itself logs the terminal
        ok/error row (regen/refresh_guild already do). The queue worker runs
        jobs one at a time; the per-job lock still serializes them against
        the nightly refresh.
        """
        start = datetime.now(timezone.utc).isoformat()
        self.db.log_job(guild_id, kind, "running", started_at=start)
        self._queue.put((guild_id, fn))

    def _worker(self):
        while True:
            _guild_id, fn = self._queue.get()
            try:
                fn()
            except Exception:  # noqa: BLE001 - job fns log their own errors; keep the worker alive
                pass

    # ---- helpers ----
    def _manifest_name(self, guild_id):
        p = self.outdir / "guilds" / f"{guild_id}.json"
        if not p.exists():
            return None
        import json

        return (json.loads(p.read_text()) or {}).get("guildName")

    def _squads_path(self, guild_id, squads_json):
        if not squads_json:
            return None
        p = self.outdir / "guilds" / f"{guild_id}.squads-config.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, squads_json)
        return p

    # ---- jobs ----
    def regen(self, guild_id, squads_json=None):
        """Regenerate squad report + dashboard + calculator + planner from existing caches (no EA calls)."""
        tb_id = TB_ID
        with self._lock:
            start = datetime.now(timezone.utc).isoformat()
            try:
                args = [guild_id, "--outdir", str(self.outdir)]
                sq = self._squads_path(guild_id, squads_json)
                if sq is not None:
                    args += ["--squads", str(sq)]
                if squads.main(args) not in (0, None):
                    raise JobError("squad_report failed")
                if dashboard.main([guild_id, "--outdir", str(self.outdir)]) not in (0, None):
                    raise JobError("render_report failed")
                # The ROTE calculator needs the TB doc, which is built from the
                # cached static gamedata; a missing doc (or failed build) skips
                # the calculator without failing the report/refresh.
                rote_path = self.outdir / "rote" / f"{tb_id}.json"
                if not rote_path.exists():
                    try:
                        tb.main([tb_id, "--outdir", str(self.outdir)])
                    except Exception as exc:  # noqa: BLE001 - calc is optional
                        print(f"[regen] TB doc unavailable; calculator skipped: {exc}", flush=True)
                if rote_path.exists():
                    if calc.main([guild_id, "--outdir", str(self.outdir), "--tb", tb_id]) not in (0, None):
                        raise JobError("rote_calc failed")
                    # The platoon planner is built from the same TB doc + guild
                    # summary; it is non-fatal (the refresh still succeeds).
                    if platoons.main([guild_id, "--outdir", str(self.outdir)]) not in (0, None):
                        print("[regen] platoon planner failed; skipped", flush=True)
                else:
                    print("[regen] ROTE calculator skipped (TB doc unavailable)", flush=True)
                self.db.log_job(guild_id, "regen", "ok", started_at=start)
                return {"guildId": guild_id, "status": "ok", "kind": "regen"}
            except Exception as exc:  # noqa: BLE001 - report as job failure
                self.db.log_job(guild_id, "regen", "error", str(exc), started_at=start)
                raise JobError(str(exc)) from exc

    def refresh_guild(self, guild_id, comlink):
        """Fetch a guild (EA via comlink) then regenerate its pages."""
        with self._lock:
            start = datetime.now(timezone.utc).isoformat()
            g = self.db.get_guild(guild_id)
            if g is None:
                raise JobError(f"guild {guild_id} is not registered")
            try:
                manifest, _summary = pipeline.refresh_guild(
                    outdir=self.outdir,
                    guild_id=guild_id,
                    comlink_url=comlink,
                    max_rps=self.max_rps,
                )
                name = (g.get("name") or "").strip() or manifest.get("guildName")
                self.db.upsert_guild(guild_id, name=name or None)
                self.db.set_last_refresh(guild_id)
                # regenerate pages from the fresh summary
                self.regen(guild_id, squads_json=g.get("squads_json"))
                self.db.log_job(guild_id, "refresh", "ok", started_at=start)
                return {"guildId": guild_id, "status": "ok", "kind": "refresh", "name": name}
            except Exception as exc:  # noqa: BLE001
                self.db.log_job(guild_id, "refresh", "error", str(exc), started_at=start)
                raise JobError(str(exc)) from exc

    def refresh_all(self, comlink):
        results = []
        for g in self.db.list_guilds():
            try:
                results.append(self.refresh_guild(g["id"], comlink))
            except JobError as exc:
                results.append({"guildId": g["id"], "status": "error", "message": str(exc)})
        return results

    # ---- nightly schedule ----
    def nightly_loop(self, stop_event, comlink, hour=4):
        """Run refresh_all once per day at `hour` (UTC). Blocks until stop_event set."""
        while not stop_event.wait(self._seconds_until(hour)):
            if stop_event.is_set():
                break
            try:
                self.refresh_all(comlink)
            except Exception:  # noqa: BLE001 - keep the loop alive
                pass

    @staticmethod
    def _seconds_until(hour):
        now = datetime.now(timezone.utc)
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()


def refresh_hour():
    try:
        return int(os.environ.get("SWGOH_REFRESH_HOUR", "4"))
    except ValueError:
        return 4
