"""Canonical store identity — one law for every file and every layer.

Store ids are STRINGS. Real exports disagree with themselves about the same
door: 9001, 9001.0 and "9001" arrive from different files of one tenant
(Excel floats its integers), and the first tenant with alphanumeric codes
("BLR-01") must not silently load an empty network — the int-typed id was
exactly that failure class (review M11). The collapse rule: numeric-looking
values normalize to their integer string; everything else is the stripped
text verbatim. Every loader, the policy, the engine's invariants and the
verifier's replay all resolve ids through this one function.
"""
from __future__ import annotations


def id_sort_key(s: str):
    """Total order over canonical ids that preserves NUMERIC order for digit
    ids — '2' sorts before '11', exactly the int order every golden plan was
    captured under — with alphanumeric ids after, lexicographically. Every
    engine tie-break that orders by store id must order by THIS, or the same
    inputs produce a different plan the day ids stop being numbers.
    Tolerates non-str ids (hand-built test inputs) by str()-ing first."""
    s = str(s)
    return (0, len(s), s) if s.isdigit() else (1, s)


def store_id(v) -> str | None:
    """None/blank -> None (the row has no id)."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return s
    return str(int(f)) if f.is_integer() else s
