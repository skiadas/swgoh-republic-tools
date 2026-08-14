#!/usr/bin/env python3
"""Static game-data source backed by the swgoh-utils/gamedata repo.

Drops in for the game-data subset of the comlink-python API (``get_game_data``
and ``get_localization``) so the game-data caches can be built without ever
touching swgoh-comlink. This sidesteps the comlink binary's baked-in V8 heap
cap, which OOMs it on the large /data responses the old cache build triggered.

Raw downloads are cached under ``<outdir>/game/static/`` and re-fetched only
when ``allVersions.json`` reports a game/locale/asset version change, so once
the files exist everything runs fully offline.

The static files mirror comlink's response shapes:
    get_game_data(items="units")    -> {"units": [...]}
    get_game_data(items="category") -> {"category": [...]}
    get_localization(unzip=True)    -> {"Loc_ENG_US.txt": "<key|value lines>"}
"""

import json
import urllib.request
from pathlib import Path

import brotli

from swgoh_reviewer.config import data_root, gamedata_base_url
from swgoh_reviewer.io import atomic_write_text

GAME_CACHE_DIR = "game"
STATIC_DIR = "static"
VERSIONS_FILE = "all-versions.json"
VERSIONS_FETCH = "allVersions.json"

# collection -> (github filename, brotli-compressed)
SOURCES = {
    "units": ("units.json.br", True),
    "category": ("category.json", False),
    "localization": ("Loc_ENG_US.txt.json.br", True),
}


def _load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class StaticGameData:
    """Comlink-compatible game-data reader backed by swgoh-utils/gamedata."""

    def __init__(self, base_url=None, outdir=None):
        self.base_url = (base_url or gamedata_base_url()).rstrip("/")
        self.outdir = Path(outdir or data_root())
        self._static_dir = self.outdir / GAME_CACHE_DIR / STATIC_DIR
        self._versions = None
        self._data = {}

    # -- raw file management -------------------------------------------

    def _fetch(self, filename):
        url = f"{self.base_url}/{filename}"
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()

    def _fetch_json(self, filename):
        return json.loads(self._fetch(filename).decode("utf-8"))

    def _versions_path(self):
        return self._static_dir / VERSIONS_FILE

    def _load_versions(self):
        if self._versions is None:
            path = self._versions_path()
            self._versions = _load_json(path) if path.exists() else {}
        return self._versions

    @staticmethod
    def _version_key(versions):
        return (
            versions.get("gameVersion"),
            versions.get("localeVersion"),
            versions.get("assetVersion"),
        )

    def _save_versions(self, versions):
        self._versions = versions
        self._static_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._versions_path(), json.dumps(versions, indent=2))

    def _source_path(self, collection):
        return self._static_dir / SOURCES[collection][0]

    def ensure_raw(self, refresh=False):
        """Cache the raw gamedata files; re-download on version change.

        With ``refresh=True`` allVersions.json is fetched fresh and any changed
        files are pulled again; a transient fetch failure falls back to the
        cached files so the pipeline keeps working offline.
        """
        fresh = None
        if refresh:
            try:
                fresh = self._fetch_json(VERSIONS_FETCH)
            except OSError:
                fresh = None

        cached = self._load_versions()
        self._changed = fresh is not None and self._version_key(fresh) != self._version_key(cached)
        if fresh is not None:
            self._save_versions(fresh)

        downloaded = False
        for collection in SOURCES:
            path = self._source_path(collection)
            if not path.exists() or (self._changed and refresh):
                self._download(collection)
                downloaded = True
        if downloaded:
            self._data.clear()

        return fresh or cached

    def _download(self, collection):
        filename, _compressed = SOURCES[collection]
        raw = self._fetch(filename)
        self._static_dir.mkdir(parents=True, exist_ok=True)
        path = self._source_path(collection)
        with open(path, "wb") as fh:
            fh.write(raw)

    # -- collection loading ---------------------------------------------

    def _read_collection_bytes(self, collection):
        return self._source_path(collection).read_bytes()

    def _load_collection(self, collection):
        if collection not in self._data:
            raw = self._read_collection_bytes(collection)
            if SOURCES[collection][1]:
                raw = brotli.decompress(raw)
            doc = json.loads(raw.decode("utf-8"))
            self._data[collection] = doc["data"] if isinstance(doc, dict) and "data" in doc else doc
        return self._data[collection]

    # -- comlink-python-compatible interface ----------------------------

    def get_game_data(self, include_pve_units=False, items=None, **kwargs):
        """Return the requested game-data collection, comlink-style.

        Only ``units`` and ``category`` are backed by the static repo; anything
        else returns an empty dict (those callers should keep using comlink).
        """
        self.ensure_raw()
        if items == "units":
            return {"units": self._load_collection("units")}
        if items == "category":
            return {"category": self._load_collection("category")}
        return {}

    def get_localization(self, locale="ENG_US", unzip=True, **kwargs):
        """Return the English localization in comlink's unzipped text shape."""
        self.ensure_raw()
        entries = self._load_collection("localization")
        text = "\n".join(f"{key}|{value}" for key, value in entries.items())
        return {"Loc_ENG_US.txt": text}
