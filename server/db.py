#!/usr/bin/env python3
"""SQLite store for service metadata: guild registry, discord links, job log.

Player/report payloads stay in JSON files under the data root; this database
holds the small, mutable operational state the service owns.
"""

import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS guilds (
    id TEXT PRIMARY KEY,
    name TEXT,
    tb_id TEXT NOT NULL DEFAULT 't05D',
    enabled INTEGER NOT NULL DEFAULT 1,
    squads_json TEXT,
    created_at TEXT,
    last_refresh TEXT
);
CREATE TABLE IF NOT EXISTS discord_links (
    discord_id TEXT PRIMARY KEY,
    allycode TEXT,
    player_id TEXT,
    linked_by TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS job_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT,
    kind TEXT,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    message TEXT
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class DB:
    def __init__(self, path):
        self.path = path
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._lock = __import__("threading").Lock()

    def _row(self, sql, params=()):
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _all(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _exec(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    # ---- guilds ----
    def list_guilds(self):
        return self._all("SELECT * FROM guilds ORDER BY name, id")

    def get_guild(self, guild_id):
        return self._row("SELECT * FROM guilds WHERE id = ?", (guild_id,))

    def upsert_guild(self, guild_id, name=None, tb_id=None, enabled=None, squads_json=None):
        g = self.get_guild(guild_id)
        if g is None:
            self._exec(
                "INSERT INTO guilds (id, name, tb_id, enabled, squads_json, created_at) VALUES (?,?,?,?,?,?)",
                (guild_id, name, tb_id or "t05D", 1 if enabled is None else enabled, squads_json, now_iso()),
            )
        else:
            fields, params = [], []
            if name is not None:
                fields.append("name = ?")
                params.append(name)
            if tb_id is not None:
                fields.append("tb_id = ?")
                params.append(tb_id)
            if enabled is not None:
                fields.append("enabled = ?")
                params.append(1 if enabled else 0)
            if squads_json is not None:
                fields.append("squads_json = ?")
                params.append(squads_json)
            if fields:
                self._exec(f"UPDATE guilds SET {', '.join(fields)} WHERE id = ?", params + [guild_id])

    def set_last_refresh(self, guild_id, when=None):
        self._exec("UPDATE guilds SET last_refresh = ? WHERE id = ?", (when or now_iso(), guild_id))

    # ---- discord links ----
    def get_discord_link(self, discord_id):
        return self._row("SELECT * FROM discord_links WHERE discord_id = ?", (discord_id,))

    def list_discord_links(self):
        return self._all("SELECT * FROM discord_links ORDER BY created_at DESC")

    def set_discord_link(self, discord_id, allycode, player_id=None, linked_by=None):
        self._exec(
            "INSERT OR REPLACE INTO discord_links (discord_id, allycode, player_id, linked_by, created_at)"
            " VALUES (?,?,?,?,?)",
            (discord_id, allycode, player_id, linked_by, now_iso()),
        )

    # ---- job log ----
    def log_job(self, guild_id, kind, status, message="", started_at=None):
        self._exec(
            "INSERT INTO job_log (guild_id, kind, started_at, finished_at, status, message)"
            " VALUES (?,?,?,?,?,?)",
            (guild_id, kind, started_at or now_iso(), now_iso(), status, message[:2000]),
        )

    def latest_job(self, guild_id=None):
        if guild_id is None:
            return self._row("SELECT * FROM job_log ORDER BY id DESC LIMIT 1")
        return self._row("SELECT * FROM job_log WHERE guild_id = ? ORDER BY id DESC LIMIT 1", (guild_id,))

    def mark_running_interrupted(self):
        """Mark any 'running' jobs as interrupted (a restart killed their worker)."""
        self._exec(
            "UPDATE job_log SET status = 'interrupted', message = 'interrupted by server restart' WHERE status = 'running'"
        )
