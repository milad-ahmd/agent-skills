---
name: ui-taste
description: Polishes an existing web UI so it looks deliberate, without a redesign. A bundled lint scores CSS, SCSS, Less, inline styles and Tailwind classes from 0 to 100 on type scale, font families, palette size and near-duplicate colours, the 4px spacing grid, radii and shadows, WCAG contrast of colour pairs set together, !important and z-index wars, Tailwind arbitrary values and generic AI-look signals (purple-blue gradients, emoji as icons, everything centred), with file:line examples. Fixes go into design tokens (CSS variables or the Tailwind theme), then it re-lints against a saved baseline and compares before and after screenshots. Use when the user asks to polish or tidy up a UI, make the design consistent, make an app look less generic, less AI-generated or less like a template, clean up messy CSS or Tailwind styles, set up a type scale, spacing scale or colour tokens, or add a design consistency lint to CI.
---

# UI Taste

Most UIs that look cheap have the same problem: too many one-off values. Six greys that should be one, eleven font sizes, padding of 13px next to 16px. Consolidating them into a few tokens fixes more than any new component library would.

## 1. Lint and save a baseline

```bash
python3 scripts/ui_lint.py src --json > /tmp/ui-before.json   # keep this for step 4
python3 scripts/ui_lint.py src                                # readable report with file:line examples
python3 scripts/ui_lint.py src --exclude 'src/legacy/*' --exclude '*theme.generated.css'
```

It reads `.css/.scss/.less`, `<style>` blocks, inline `style` attributes and objects, styled/css template literals, and Tailwind classes in `class`/`className` and `cn()`/`clsx()`/`cva()` calls in `.html/.vue/.svelte/.astro/.jsx/.tsx` (`--include-js` adds `.js/.ts`). It skips `node_modules`, `dist`, `build`, `vendor`, `*.min.*`, minified files and CSS with a `/*!` license banner. Exclude generated themes yourself: a 20,000-line UI kit stylesheet will swamp the score.

| category | pts | full marks when |
|---|---:|---|
| type scale | 15 | 8 or fewer distinct font sizes (px, rem and pt normalised at a 16px root) |
| fonts | 10 | 2 or fewer primary families |
| colours | 20 | 16 or fewer distinct colours, no near-duplicates (CIE76 delta-E <= 3), at most 4 hue groups (one accent plus status colours) |
| spacing | 15 | margin, padding and gap on the 4px grid (`--grid 8` for an 8px system). Tailwind scale classes count as on-grid |
| radii/shadows | 10 | 4 or fewer of each |
| contrast | 10 | colour and background set in the same rule or element pass WCAG AA (4.5:1, or 3:1 for large text) |
| overrides | 8 | rare `!important`, no z-index above 1000, at most 6 z-index values |
| arbitrary values | 7 | few Tailwind `p-[13px]`, `text-[#3b82f6]` style values (`[var(--x)]` is fine) |
| generic look | 5 | no purple/blue gradient, under 4 gradients, under 3 emoji in markup, not nearly everything centred |

Each category scores weight x health (0 to 1); the exact formulas are in `--help`. A category with nothing to measure gets full marks and shows `n/a`. Exit codes: `0` ok · `1` score below `--fail-below N` · `2` nothing to scan.

**Limits:** it scans with regexes. It does not see the cascade, media queries, the Tailwind config or the rendered page. Contrast is only checked where it can resolve both colours (literals, `var()`, shadcn-style `--x: 222 47% 11%` tokens, Tailwind classes that map to a CSS variable). Light-theme tokens are measured and `.dark` overrides are skipped.

## 2. Look at the page

If a browser or screenshot tool is available, capture the main screens at desktop width and at 375px before you change anything. Then check what the lint can't see.

Each view should have one primary action. Two filled accent buttons side by side means neither of them is primary. Body text should be left-aligned inside one container width, with edges that line up from section to section. Paragraphs read best at 45 to 75 characters per line (`max-width: 65ch` or `max-w-prose`). The generic look also shows up in layout: a gradient hero over a centred stack of identical cards, emoji standing in for icons, glass blur on every panel.

## 3. Fix by consolidating into tokens

Start with the categories that lost the most points, and fix each one as a set instead of patching values one at a time.

1. Pick the scale from what is already there. The report lists every font size in use, so keep the common ones as steps (for example 12/14/16/18/20/24/30/36) and move the rare ones to the nearest step. Do the same for spacing in multiples of 4, radii (say 4, 8 and full) and shadows (sm, md, lg).
2. Write the tokens once, as custom properties on `:root` for plain CSS, `@theme { --color-brand: ...; }` in the CSS entry file for Tailwind v4, `theme.extend` in `tailwind.config.js` for Tailwind v3, or a `_tokens.scss` partial.
3. Replace the literals with the tokens. In each near-duplicate group keep the most used value, which the report lists first. Once `brand` is in the theme, `text-[#3b82f6]` becomes `text-brand`.
4. Take neutrals from one family (not `gray` and `slate` both), keep one accent, and use red, green and amber only for status. Fix contrast by darkening the text, not by changing the brand colour.
5. Swap `z-index: 99999` for a short named set (dropdown, sticky, modal, toast). Remove an `!important` once the selector it was fighting is fixed.

## 4. Re-lint and compare

```bash
python3 scripts/ui_lint.py src --baseline /tmp/ui-before.json   # Score: 81/100 (was 46, +35), with per-category change
python3 scripts/ui_lint.py src --fail-below 70                   # CI gate once the team agrees on a floor
```

Take the same screenshots again and compare them side by side. The page should look calmer and the same overall. If the layout moved or a brand colour changed, revert that part.

## Report

Before and after score per category, the tokens you added, the five biggest changes with file:line, before and after screenshots if you took them, and what you left alone and why (for example a 13px value that aligns an icon on purpose).

## Rules

Don't change brand colours, fonts or the layout structure without asking; propose the change and show it. Don't add a UI library or CSS framework to get consistency. Don't chase the score: a one-off value that is right for the design stays, with a line in the report saying why. Leave vendored, generated and third-party styles alone and exclude them from the lint. For a full accessibility audit (keyboard, screen readers, ARIA) use a11y-fixer; this skill only checks colour contrast.
