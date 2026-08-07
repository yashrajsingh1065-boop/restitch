# Design

Light theme only. Scene: a buyer at an office desk mid-morning, large monitor,
daylight, Excel on the second screen. The app must read as the same product as
the workbook it renders — the palette below IS the workbook palette
(render/style.py), lifted to the screen with OKLCH-tuned neutrals.

## Color

Strategy: **Restrained**. Tinted slate neutrals; navy reserved for structure
(title bands, primary actions); teal/green/amber/maroon are semantic only and
never decorative. No pure black or white anywhere.

- `--bg`            oklch(98.6% 0.003 250)   /* page, ≈ workbook BAND #F8FAFC */
- `--surface`       oklch(99.4% 0.002 250)   /* panels, table bodies */
- `--surface-2`     oklch(96.8% 0.005 250)   /* toolbars, tile fill, ≈ LSLATE */
- `--line`          oklch(92.9% 0.007 250)   /* hairlines, ≈ LSTEEL #E2E8F0 */
- `--ink`           oklch(20.8% 0.02 260)    /* body text, ≈ INK #0F172A */
- `--mute`          oklch(55.4% 0.023 257)   /* secondary text, ≈ MUTE #64748B */
- `--navy`          oklch(28.6% 0.045 265)   /* structure + primary, ≈ #1F2A44 */
- `--slate`         oklch(37.2% 0.033 259)   /* table headers, ≈ #334155 */
- `--teal`          oklch(51.4% 0.086 194)   /* info accents, ≈ #0F766E */
- `--green`         oklch(52.7% 0.137 150)   /* PASS, ≈ #15803D */
- `--amber`         oklch(55.3% 0.121 66)    /* AMBER/warn, ≈ #B45309 */
- `--maroon`        oklch(45.8% 0.171 13)    /* FAIL/red, ≈ #9F1239 */
- `--gold-bg`       oklch(96.4% 0.013 97)    /* warn callout fill, ≈ #F5F3E7 */
- `--green-bg`      oklch(97.9% 0.014 165)   /* good callout fill */
- `--red-bg`        oklch(97.1% 0.013 17)    /* blocked callout fill */

State tints: hover = mix 4% navy into surface; selected = mix 8%; focus ring
2px `--teal` outside a 1px offset; disabled = 45% opacity, never hue shift.

## Typography

One family: `-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui,
sans-serif`. Numbers in tables and tiles use `font-variant-numeric:
tabular-nums` always.

Fixed rem scale, ratio 1.2: 12 / 13 (body) / 15.5 / 18.5 / 22.5 / 27 (page
title). Weight contrast does the hierarchy work: 400 body, 600 labels and
table headers, 700 page titles and KPI values. Table headers are 11px
uppercase +0.04em tracking on slate fill, white text — the workbook's
header_row, verbatim. Prose (callouts, evidence) caps at 72ch.

## Components

- **Title band** — every page opens with the workbook title_block: navy bar,
  white 600 title, slate sub-bar with the provenance line (period tags, file
  dates). Not a hero; a header.
- **KPI tile** — the workbook kpi(): light slate fill, 1px line, small mute
  label / large tabular value / small steel sub-line. Value color = semantic
  meaning only.
- **Table** — slate header band, zebra rows (`--surface` / `--bg`), hairline
  borders, right-aligned tabular numbers, row hover tint. Dense by default.
- **Callout** — full-width tinted band with 1px full border (info slate /
  warn gold / good green / blocked red), prose inside, label word first.
  Never a side-stripe.
- **Check row** — status word (PASS/FAIL/AMBER/SKIPPED, 700, semantic color)
  + check title + expandable evidence line in mute. The report's atom.
- **Lever row** — label + control + current value + "preset: X" mute tag +
  one-line cost note. A changed-from-preset lever gets a navy dot before the
  label and the diff line appears in the review step.
- **Buttons** — primary: navy fill, white text; secondary: surface fill, 1px
  line; destructive/overrule: maroon OUTLINE (overruling a red finding is
  typed, never one-click). All: 2px radius, 150ms background transition,
  visible focus ring, disabled at 45%.
- **Progress** — phase list A–E as stepped rows with state words (waiting /
  running / done), not a bar; skeleton rows for pending tables.
- **Wizard rail** — left rail listing the six run steps with done/current/
  locked states; steps are pages, the rail is the map.

## Layout

App shell: slim top bar (wordmark "restitch" lowercase 600, run-id crumb,
nothing else) over a single content column, max 1200px, 24px gutters. The
new-run flow adds the left step rail (200px). Tables may bleed to 100% of the
column. Spacing scale 4/8/12/16/24/40; section gaps 40px, intra-card 12–16px.
No nested cards; most sections are rules-and-whitespace, not boxes.

## Motion

150–200ms ease-out on hover/focus/expand. Check-evidence expand animates
height only via grid-template-rows trick (no layout-prop animation). Phase
transitions: text state change + 150ms fade. `prefers-reduced-motion`: all
transitions to 0ms, progress states step without fade. Nothing else moves.
