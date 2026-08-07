"""Independent post-save verification — replays the SAVED workbook.

Imports NOTHING from the engine, the localizer, the io stack or the renderer
(enforced by an AST test): only the domain model, the policy carrier, the rule
registry and the battery structure. The caller loads inputs through whatever
independent path it trusts; this package re-derives every rule from those
inputs + the workbook on disk alone. If the file has drifted from what the
build verified — serialization bug, stale file, hand edit — this catches it.
"""
from .replay import verify_workbook

__all__ = ["verify_workbook"]
