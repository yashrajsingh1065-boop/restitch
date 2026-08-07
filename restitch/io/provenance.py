"""Provenance — every run records exactly what produced it.

A plan is only trustworthy if it can name its inputs: the sha256 of every file,
the fully resolved policy (after presets, overrides and manifest-derived dates),
and the package version. The record is stamped into the run directory next to
the plan; reproducing a run is re-running its manifest and comparing hashes.
"""
from __future__ import annotations

import datetime
import hashlib
from pathlib import Path

from ..core.policy import Policy, to_dict


def file_sha256(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def build_provenance(files: dict[str, list[Path]], pol: Policy, *,
                     soh_asof=None, version: str | None = None) -> dict:
    """files = role -> [paths]. Returns the JSON-safe provenance record."""
    if version is None:
        try:
            from importlib.metadata import version as _v
            version = _v("restitch")
        except Exception:
            version = "unpackaged"
    rec_files = {}
    for role, paths in files.items():
        rec_files[role] = [
            dict(path=str(p), sha256=file_sha256(p), bytes=Path(p).stat().st_size)
            for p in paths]
    return dict(
        restitch_version=version,
        created=datetime.datetime.now().isoformat(timespec="seconds"),
        soh_asof=(soh_asof.isoformat() if soh_asof is not None else None),
        files=rec_files,
        policy=to_dict(pol),
    )
