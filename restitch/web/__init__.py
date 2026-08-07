"""Local web console for restitch — FastAPI + Jinja, no SPA, no database.

The filesystem is the database: every run is a self-contained provenance
bundle under the runs root (inputs/, mappings/, manifest.yaml, policy.yaml,
overrides.yaml, status.json, log.txt, out/). Reproducing any run is
`restitch run --manifest <rundir>/manifest.yaml`; the app is a console over
exactly that pipeline, never a second one.
"""
from .app import create_app

__all__ = ["create_app"]
