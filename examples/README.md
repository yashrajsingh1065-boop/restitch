# Example profiles

Two complete, working configuration sets — the same files `restitch demo`
generates, committed here so you can read them without running anything.

| Directory | Category shape | What it demonstrates |
|---|---|---|
| `tailoring/` | numeric drop ladder (`084`–`108`), zero-padded | the default demo: numeric-string store ids, extended money columns, per-sheet division filters |
| `footwear-uk/` | UK half-size ladder (`6`–`10`) written as floats in the raw file | the `uk_shoe` size normalizer and **alphanumeric store ids** (`FW-9001`) end to end |

Each set is:

- `policy.yaml` — the category preset: size vocabulary, pivotal set, every
  lever's default. The levers step (CLI flags or web console) overrides any
  lever without touching this file.
- `mappings/*.yaml` — one profile per input role, mapping the raw file's own
  headers onto canonical fields, plus semantics (money-column extendedness,
  category value maps, junk-row `id_pattern`, …).
- `manifest.yaml` — names every file + profile pair; `restitch run
  --manifest` takes exactly this. Note the paths are relative to the demo
  output directory — regenerate live copies with
  `restitch demo --vocab tailoring` (or `--vocab footwear-uk`).

Adapting to your own exports: run `restitch introspect YOURFILE.xlsx` to see
its headers, start from the nearest profile here, and let the loud loader
tell you what still doesn't fit. Nothing loads through a guess.
