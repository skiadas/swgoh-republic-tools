"""Fetch + downscale SWGOH unit portrait assets.

Source art is the 2D textures extracted from the game's AssetBundles by a
running ``swgoh-ae2`` instance (a compose service, or ``SWGOH_ASSET_BASE``).
Each unit is fetched once, downscaled to 256px WebP and written to
``<outdir>/game/assets/<baseId>.webp``; the cell templates reference those
files and fall back to the plain-name cell when a file is missing.

Fetches are never fatal: a missing/unreachable extractor just leaves the
corresponding thumbnails absent, and the nightly/manual asset pass tops them up
the next time it runs.
"""

import io
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from swgoh_reviewer.config import asset_base_url, data_root
from swgoh_reviewer.io import atomic_write_bytes

log = logging.getLogger(__name__)

GAME_ASSETS_DIR = "game/assets"
TARGET_SIZE_PX = 256
# ae2 bundles are named after a per-unit texture: charui_* for characters,
# shipui_* for ships. baseId is uppercased in game data; bundle names are
# lowercase. Rare units whose bundle name differs from this rule go in here.
ASSET_NAME_OVERRIDES = {}

CHAR_PREFIX = "charui_"
SHIP_PREFIX = "shipui_"


def assets_dir(outdir):
    return Path(outdir) / GAME_ASSETS_DIR


def load_unit_shape(outdir):
    """{baseId: (combatType, asset_name)} for every known unit.

    asset_name comes from the game's authoritative thumbnailName (the `thumb`
    port in the compact cache), falling back to the naive charui_/shipui_ rule
    for units without one. The cache's combatType is returned so callers keep
    the raw value for their own purposes.
    """
    units_path = Path(outdir) / "game" / "units.json"
    if units_path.exists():
        units = json.loads(units_path.read_text())
        shape = {}
        for base_id, info in units.items():
            info = info or {}
            thumb = info.get("thumb")
            ct = info.get("combatType", 1)
            asset = thumb if thumb else asset_name(base_id, ct)
            shape[base_id] = (ct, asset)
        return shape
    names_path = Path(outdir) / "names.json"
    if names_path.exists():
        names = json.loads(names_path.read_text())
        return {base_id: (1, asset_name(base_id, 1)) for base_id in names}
    return {}


def asset_version(outdir):
    """The current game asset batch (ae2 downloads bundles keyed by this).

    Source is the static gamedata manifest (all-versions.json -> assetVersion),
    written by build_caches; falls back to a recent known-good version. ae2's
    /Asset/single requires this — with version 0 the CDN path 404s/403s.
    """
    versions_path = Path(outdir) / "game" / "static" / "all-versions.json"
    if versions_path.exists():
        try:
            manifest = json.loads(versions_path.read_text())
            version = manifest.get("assetVersion")
            if version is not None:
                return str(version)
        except (ValueError, OSError):
            pass
    return "100044"


def asset_name(base_id, combat_type):
    """The ae2 bundle name that holds a unit's portrait texture."""
    base_id = (base_id or "").upper()
    if base_id in ASSET_NAME_OVERRIDES:
        return ASSET_NAME_OVERRIDES[base_id]
    prefix = SHIP_PREFIX if combat_type == 2 else CHAR_PREFIX
    return prefix + base_id.lower()


def fetch_assets(outdir=None, force=False, base_url=None, progress=print):
    """Fetch missing (or, with force, all) unit portraits into the assets dir.

    Returns (fetched, missed) counts; never raises for missing assets. Images
    are downscaled with Pillow (lazy import so a missing Pillow keeps the whole
    pipeline functional).
    """
    from PIL import Image

    outdir = Path(outdir or data_root())
    base_url = (base_url if base_url is not None else asset_base_url()).rstrip("/")
    out = assets_dir(outdir)
    out.mkdir(parents=True, exist_ok=True)

    shape = load_unit_shape(outdir)
    version = asset_version(outdir)
    # Deterministic order so repeated passes emit the same misses.
    fetched = missed = 0
    for base_id in sorted(shape):
        _ct, name = shape[base_id]
        dest = out / f"{base_id}.webp"
        if dest.exists() and not force:
            continue
        qs = urllib.parse.urlencode(
            {"forceReDownload": str(bool(force)).lower(), "assetName": name, "version": version}
        )
        try:
            with urllib.request.urlopen(f"{base_url}/Asset/single?{qs}", timeout=30) as resp:
                raw = resp.read()
            img = Image.open(io.BytesIO(raw))
            img.load()
            img = img.convert("RGBA")
            img.thumbnail((TARGET_SIZE_PX, TARGET_SIZE_PX))
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=80)
            atomic_write_bytes(dest, buf.getvalue())
            fetched += 1
            progress(f"[assets] {base_id} -> {dest.name}")
        except Exception as exc:  # noqa: BLE001 - per-asset miss is not fatal
            missed += 1
            log.warning("asset fetch failed for %s (%s): %s", base_id, name, exc)
    return fetched, missed


def ensure_images(outdir=None, force=False, progress=print):
    """Top-up the portrait cache after a game-data rebuild; never raises."""
    if not asset_base_url():
        progress("[assets] SWGOH_ASSET_BASE empty — skipping unit portraits")
        return
    try:
        fetched, missed = fetch_assets(outdir=outdir, force=force, progress=progress)
    except Exception as exc:  # noqa: BLE001 - optional companion to the game data
        log.warning("asset pass skipped: %s", exc)
        progress(f"[assets] skipped: {exc}")
        return
    progress(f"[assets] {fetched} fetched, {missed} missed")