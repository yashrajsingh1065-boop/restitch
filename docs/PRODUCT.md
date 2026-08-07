# Product

## Register

product

## Users

A retail buyer (or their merchandiser) at an office desk mid-morning, large
monitor, between SAP exports and Excel. They have just pulled fresh SOH, norms
and velocity files and need a checked store-to-store movement plan. They will
be asked to defend every line of it: to a brand head, to store ops, to
themselves in three weeks. The job on any given screen is one of three:
start a run from new files, decide whether to trust a finished run, or price a
lever by comparing two runs.

They are Excel-native and numerate. They do not read manuals; they read
evidence. A number without its period tag or its derivation is a number they
distrust, correctly.

## Product Purpose

restitch turns raw category exports into a fully proven redeployment workbook.
The web app is the local console for that engine: runs list, guided new-run
flow (files → mapping → sanity → levers → run), a checks-first report, and
run-vs-run comparison. Success: the buyer executes a 14,000-unit transfer plan
without opening a terminal, and every constraint's cost is visible before they
commit. The workbook remains the deliverable; the app is how it is produced
and trusted.

## Brand Personality

Evidential, calm, exact. The voice of a careful analyst who shows the working:
never salesy, never alarmed, never vague. Numbers carry the emotion; prose
explains the mechanism. Three words: proven, legible, unhurried.

## Anti-references

- SaaS dashboard theatre: hero metrics with gradient accents, celebratory
  empty states, onboarding confetti. This is a working tool, not a pitch.
- Dark "dev tool" cosplay. The scene is a bright office next to Excel; a dark
  theme would make the app read as a different product than its own workbook.
- BI-tool chart walls. The engine's output is a plan, not a dashboard; charts
  appear only where a delta needs a shape.
- Modal-driven flows. A run is a sequence of decisions with evidence; each
  gets a page, never a popover.
- Spinner-in-the-void loading. Phases A–E are real and nameable; show them.

## Design Principles

1. **The workbook is the brand.** The app shares one visual vocabulary with
   the Excel deliverable it produces: navy title bands, slate table headers,
   zebra rows, KPI tiles, evidence-first checks. A buyer moving between the
   report page and sheet 8 should feel zero costume change.
2. **Checks before numbers, numbers before prose.** Every report leads with
   what was proven ("43 checks · 22 verify"), then the KPI deltas, then the
   download. Trust is the product; the order of information is the argument.
3. **Every lever states its price.** A control the buyer can change shows its
   value, its preset default, and what deviating cost on measured history.
   No naked toggles.
4. **Degradation is loud, never silent.** Missing files, skipped rules,
   amber sanity findings are surfaced in place with their consequences,
   in the same breath as the things that passed.
5. **Rerun is the habit, wizard the exception.** The home screen optimizes
   for "same profile, fresh files, go" in three clicks; the full guided flow
   exists for the first run of a category, not every run.

## Accessibility & Inclusion

WCAG 2.1 AA. Semantic state colors always paired with a text label (PASS /
AMBER / FAIL / SKIPPED words, never color alone — the workbook already obeys
this). Full keyboard operability for the run flow. `prefers-reduced-motion`
respected: progress states swap animation for stepped text. Dense tables keep
row hover + focus outlines. No information conveyed by hue alone anywhere.
