---
name: a11y-fixer
description: Audits web and mobile UI code against WCAG 2.2 AA and fixes accessibility issues directly in the source, including alt text, labels, contrast, keyboard navigation, focus, ARIA, and semantics. A read-only accessibility scan of HTML, JSX/TSX, Vue and Svelte components reports missing alt text, unlabelled form controls, nameless buttons and links, missing page lang, untitled iframes, positive tabindex, autoplaying media, clickable divs with no keyboard support, aria-hidden on focusable elements, invalid ARIA roles and skipped heading levels, each with its source location, the WCAG success criterion and a confidence level, and can fail CI. Use when the user asks for an accessibility audit, a11y fixes, WCAG compliance, or screen-reader support.
---

# A11y Fixer

Find accessibility issues **and fix them in code**. Don't just list them.

## 1. Scan

```bash
python3 scripts/a11y_scan.py src/                   # findings grouped by file
python3 scripts/a11y_scan.py --json . > a11y.json   # every finding with WCAG links
python3 scripts/a11y_scan.py --js src/              # also read .js files as JSX (Create React App style)
python3 scripts/a11y_scan.py --fail-on medium .     # CI gate: exit 1 on medium or high
```

The script only reads files and needs no network. It covers `.html .htm .jsx .tsx .vue .svelte` (Vue: the `<template>` block; Svelte: markup outside `<script>`/`<style>`) and skips `node_modules`, build output, minified files and test/spec files.

| Rule | Finds | WCAG 2.2 |
|---|---|---|
| `img-alt`, `input-image-alt`, `area-alt` | image, image button or map area with no text alternative | 1.1.1 |
| `alt-not-alternative` | alt that is a filename, a placeholder like "spacer" or a number like "photo 3" | 1.1.1 |
| `control-label` | input, select or textarea with no label, aria-label(ledby), title or placeholder | 4.1.2, 3.3.2 |
| `button-name`, `link-name` | empty or icon-only button / `a[href]` / Link component | 4.1.2, 2.4.4 |
| `html-lang`, `iframe-title` | `<html>` without lang, iframe without title | 3.1.1, 4.1.2 |
| `tabindex-positive`, `media-autoplay` | tabindex > 0; autoplay without muted or controls | 2.4.3, 1.4.2 |
| `click-no-keyboard` | div/span/li/i/... with onClick, `@click` or `on:click` and no role or key handler | 2.1.1, 4.1.2 |
| `aria-hidden-focus`, `role-invalid` | aria-hidden on or around focusable elements; unknown or abstract role | 4.1.2 |
| `heading-skip` | heading level jumps by more than one in a file (advisory) | 1.3.1 |

| Confidence | Meaning |
|---|---|
| high | the markup proves it: nothing in the file could supply the missing name, label or alt |
| medium | very likely: an icon component inside a button, a positive tabindex, autoplay that may have no sound, an invalid role, a label that may come from a parent component |
| low | the scan could not see everything (spread props, dynamic ids, component children, a click handler wrapping a link) or the rule is advisory |

Exit codes: `0` nothing at or above `--fail-on` (default `high`) · `1` findings at or above it · `2` bad arguments, a missing path or no supported files.

**What it cannot see.** Component files go through a hand-written tokenizer, not a JS parser, so unusual syntax can hide an element. Values computed at runtime (`{expr}`, `:attr`, `[attr]`, `{{ }}`) count as present with an unknown value. Components are never checked themselves, and a label or name passed in from a parent is invisible. Handlers added in script (`addEventListener`), anything in CSS (**colour contrast**, focus outlines, `display:none`, target size) and the rendered DOM are out of scope. Every rule's source is in `references/sources.md`.

Also run whatever the project already has: `eslint-plugin-jsx-a11y`, Svelte's compiler warnings, `npx @axe-core/cli <url>` or Lighthouse against a running page (the only way to check contrast).

## 2. Verify, then review what no scanner sees

Open every finding at `file:line` before fixing it. For medium and low ones, read the component: does the icon render a `<title>`, does the parent pass `aria-label` through the spread, does a `<label>` in the parent point at this input? Drop the ones that turn out fine and say why.

Then review the components by hand against this checklist:

| Area | Check (WCAG 2.2 AA) |
|---|---|
| Images | Meaningful `alt`; decorative images get `alt=""`; icon buttons have accessible names |
| Forms | Every input has a `<label>`/`aria-label`; errors linked via `aria-describedby`; required fields marked |
| Semantics | Real `<button>`/`<a>` (no clickable `<div>`), one `<h1>`, ordered headings, landmarks (`main`, `nav`) |
| Keyboard | Everything reachable & operable with Tab/Enter/Space/Esc; no keyboard traps; logical order |
| Focus | Visible focus style (never bare `outline: none`); focus moves into modals and returns on close |
| Color | Text contrast ≥ 4.5:1 (3:1 large text/UI); info not conveyed by color alone |
| ARIA | Prefer native HTML; no invalid roles; `aria-expanded`/`aria-controls` on toggles; live regions for async updates |
| Motion & media | Respect `prefers-reduced-motion`; captions for video; no autoplay with sound |
| Targets | Touch targets ≥ 24×24 CSS px (44pt iOS / 48dp Android recommended) |
| Mobile | React Native `accessibilityLabel`/`accessibilityRole`; Flutter `Semantics`; SwiftUI `.accessibilityLabel` |

The scan does not read React Native, Flutter or SwiftUI code; review those with the Mobile row.

## 3. Report

A table with severity (blocker, serious or moderate), `file:line`, the WCAG criterion and the fix. Mark which rows came from the scan (with the rule id and confidence) and which from manual review. Severity is your judgement of user impact; confidence is how sure the scan is that the issue exists.

## 4. Fix

- Apply the fixes in the project's own style: existing components, design tokens, i18n for any new strings.
- Turn a clickable `<div>` into a real `<button>` rather than adding `role`, `tabindex` and key handlers. Native elements beat ARIA.
- Write real alt text from what the image shows in context. Don't invent descriptions you cannot see; ask, or leave a TODO the user can find.
- For contrast problems, suggest the closest compliant color from the existing palette.
- Don't change visual design beyond what compliance requires. Flag larger design decisions for the user instead.
- Re-run the scan and the project's linters and tests afterwards. The fixed findings should be gone; explain any that remain. Report what was fixed and what still needs manual testing with a screen reader (VoiceOver, NVDA or TalkBack).
