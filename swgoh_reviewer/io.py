#!/usr/bin/env python3
"""Atomic file writes.

Artifacts (summaries, reports, HTML pages) are served by the web layer while
the nightly refresh regenerates them. Writing via Path.write_text truncates
the destination first, so a concurrent reader can catch a partial file.
These helpers write to a temp file in the same directory and os.replace it,
so readers always see a complete old-or-new file.
"""

import os
import tempfile
from pathlib import Path


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
