---
name: a11y-fixer
description: Audits web and mobile UI code against WCAG 2.2 AA and fixes accessibility issues directly in the source, including alt text, labels, contrast, keyboard navigation, focus, ARIA, and semantics. Use when the user asks for an accessibility audit, a11y fixes, WCAG compliance, or screen-reader support.
---

# A11y Fixer

Find accessibility issues **and fix them in code**. Don't just list them.

## 1. Audit

Run any available tooling first: `eslint-plugin-jsx-a11y`, `npx @axe-core/cli <url>`, Lighthouse, or the platform's accessibility inspector.
Then review the components manually against this checklist:

| Area | Check (WCAG 2.2 AA) |
|---|---|
| Images | Meaningful `alt`; decorative → `alt=""`; icon buttons have accessible names |
| Forms | Every input has a `<label>`/`aria-label`; errors linked via `aria-describedby`; required fields marked |
| Semantics | Real `<button>`/`<a>` (no clickable `<div>`), one `<h1>`, ordered headings, landmarks (`main`, `nav`) |
| Keyboard | Everything reachable & operable with Tab/Enter/Space/Esc; no keyboard traps; logical order |
| Focus | Visible focus style (never bare `outline: none`); focus moves into modals and returns on close |
| Color | Text contrast ≥ 4.5:1 (3:1 large text/UI); info not conveyed by color alone |
| ARIA | Prefer native HTML; no invalid roles; `aria-expanded`/`aria-controls` on toggles; live regions for async updates |
| Motion & media | Respect `prefers-reduced-motion`; captions for video; no autoplay with sound |
| Targets | Touch targets ≥ 24×24 CSS px (44pt iOS / 48dp Android recommended) |
| Mobile | React Native `accessibilityLabel`/`accessibilityRole`; Flutter `Semantics`; SwiftUI `.accessibilityLabel` |

## 2. Report

A table with severity (🔴 blocks users · 🟠 serious · 🟡 moderate), `file:line`, the WCAG criterion and the fix.

## 3. Fix

- Apply the fixes in the project's own style: existing components, design tokens, i18n for any new strings.
- For contrast problems, suggest the closest compliant color from the existing palette.
- Don't change visual design beyond what compliance requires. Flag larger design decisions for the user instead.
- Re-run the linters and tests afterwards. Report what was fixed and what still needs manual testing with a screen reader (VoiceOver, NVDA or TalkBack).
