# restitch

**Turn cut-size inventory back into sellable full sets — with a transfer plan
that proves itself.**

A size-set redeployment engine for multi-door retail. A "full set" is a store
holding every pivotal size of a style; a "cut" set is missing one — and a suit
rack without the middle sizes barely sells. restitch reads your own system's
exports (stock, store master, norms, velocity), finds every cut set in the
network, and produces a store-to-store movement workbook where every line is
justified by velocity, every constraint is priced, and the whole plan is
re-verified by an independent replay of the saved file before anyone executes
it.

```
pip install restitch          # + restitch[web] for the local web console
```

## Sixty seconds

```
$ restitch demo

demo bundle written to restitch-demo/ (raw files + mapping profiles +
policy.yaml + manifest.yaml)

[!] AMBER bulk_outlier: 1 store x style sales figures are >=5x their style's
      own median — possible bulk/institutional orders
      JKT003 @ store 9009: 40 u in the window vs style median 3 (13x)
[·] INFO  excluded_ledger: 47 of 1,850 stock units (2.5%) are excluded from
      the plan universe

running engine ...

plan: 73 lines · 103 units · 55 doors · categories: Jacket, Trouser
programs: EXT 33, HEAL 9, HEAL3 1, SET 53, TOPUP 7
cut units: 57.5% before -> 52.5% after P1 -> 52.5% after full plan
shipping:  38,012 km -> 37,048 km (2 donor legs re-paired)
rules:     13 active · 3 skipped (door_freeze_afs, geo_scope, style_freeze)

rendering workbook -> movement.xlsx ...
build battery: 42 checks: 39 passed, 3 skipped
independent verify (replays the SAVED workbook) ...
verify: 29 checks: 26 passed, 3 skipped
```

The demo writes a deliberately messy synthetic bundle — a junk banner row in
the master, extended money columns, a planted bulk outlier, a liquidation
outlet that must be excluded — and runs it through the exact pipeline your
real files take. Nothing about the demo path is special.

## The proof chain

Most allocation tools print a plan and ask to be believed. restitch's output
must survive three independent layers before a workbook exists at all:

1. **29 engine invariants** assert on the engine's own state — conservation,
   no donor over-draw, floors and caps, budget banding, geography. A violated
   invariant crashes the run; there is no "warning" mode.
2. **The build battery (42 checks)** replays the *produced rows* — the
   renderer refuses to save a workbook carrying a failed check.
3. **The independent verifier (29 checks)** re-opens the SAVED .xlsx with a
   separately loaded copy of the inputs and re-derives everything — including
   a geo-constrained optimality re-solve of donor localization. Its
   independence is AST-enforced: the verifier package cannot import the
   engine, the localizer, the loaders or the renderer.

Every check lands in the report with its evidence, skipped rows shown in
place (a skipped check is a rule you disabled, never a dropped row). A run
directory is a self-contained provenance bundle — input sha256s, the resolved
policy, the canonical move list — and `restitch verify RUNDIR` re-runs the
manifest and refuses if anything drifted.

## Constraints are the product

Run the demo with every guard off and the engine happily moves five times
the units at six times the shipping:

| | lines | units moved | cut % after | shipping km |
|---|---|---|---|---|
| **default preset** | 73 | 103 | 57.5% → **52.5%** | 37,048 |
| **`--preset permissive`** (ceiling) | 390 | 533 | 57.5% → **24.9%** | 229,773 |

The permissive number is the *costing ceiling*, not a target: the gap between
52.5% and 24.9% is what your floors, caps, RoS gates, franchise rules and
budget cost — and every lever states its own share of that price. Constrain
geography and the report prices the scope by a deterministic re-run with
geography open. That is the product: not the biggest plan, but a plan where
every unit moved and every unit *not* moved has a named reason.

## Your own data

1. `restitch introspect YOUR_STOCK.xlsx` — see the file's own headers and
   sample values.
2. `restitch map YOUR_STOCK.xlsx --role soh --out soh.yaml` — draft a mapping
   profile; every suggested header needs a human eye before first use.
   Start from a committed example in [`examples/`](examples/) — the
   `footwear-uk` set shows alphanumeric store ids and a float size ladder.
3. Write a `manifest.yaml` naming every file + profile pair (copy the demo's)
   and a `policy.yaml` preset for your category's size vocabulary.
4. `restitch run --manifest manifest.yaml --policy policy.yaml` — input
   sanity runs first (RED findings block; overruling one is an explicit,
   recorded act), then engine → workbook → independent verify.

Or `restitch serve` for the local web console: guided first run, mapping
dropdowns on mismatch, priced levers, checks-first report, rerun/duplicate,
and honest run comparison (input identity proven by sha256 before a delta is
allowed to mean anything). No database — the filesystem of run directories is
the state.

## As a library

```python
from restitch import (policy_from_dict, load_manifest, resolve, run,
                      render_workbook, verify_workbook)
import yaml

rr = resolve(load_manifest("manifest.yaml"),
             policy_from_dict(yaml.safe_load(open("policy.yaml"))))
R = run(rr.inputs, rr.policy)                       # asserts 29 invariants
battery = render_workbook(R, "movement.xlsx")       # refuses on failed checks
proof = verify_workbook("movement.xlsx", rr.inputs, rr.policy)
print(proof.summary())
```

## Design notes

- [docs/PRODUCT.md](docs/PRODUCT.md) — who it serves and the five product
  principles (checks before numbers; every lever states its price;
  degradation is loud; rerun is the habit).
- [docs/DESIGN.md](docs/DESIGN.md) — the console's visual system ("the
  workbook is the brand").
- Store ids are canonical strings with one collation law; sizes are strings
  through one normalizer per profile; money columns are presumed extended
  until the profile declares otherwise. Every such rule exists because the
  silent version once produced a confidently wrong plan.

## Status

Beta. The kernel is golden-master pinned against the production run of the
source project it was extracted from (13,542 moves, byte-exact through three
independent load paths), and the public tree carries its own plan-content
golden. Python ≥ 3.11. MIT license.
