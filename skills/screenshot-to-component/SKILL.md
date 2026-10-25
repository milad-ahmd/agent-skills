---
name: screenshot-to-component
description: Converts a screenshot, mockup, or Figma export into a production-ready UI component that reuses the project's existing design system (React/Next, Vue, Svelte, React Native, Flutter, or SwiftUI with Tailwind, CSS modules, or a component library), so the result is responsive and accessible. Use when the user shares an image of a UI and asks to build, implement, or recreate it in code.
---

# Screenshot to Component

From a picture to code that looks like *your* team wrote it.

## 1. Learn the project's UI conventions first

- Framework and styling: Tailwind (read `tailwind.config.*` for theme tokens), CSS modules, styled-components, a component library (shadcn/ui, MUI, Chakra, Ant), React Native StyleSheet, Flutter `ThemeData`.
- **Existing components** to reuse: `Button`, `Card`, `Input`, `Avatar`, `Icon`, `Modal`... Search `components/` and `ui/`.
- Design tokens: colors, spacing scale, radii, typography, dark mode, and the icon library in use (lucide, heroicons...).
- File naming, export style, prop typing and the i18n approach.

## 2. Analyse the image

Break it into a component tree before writing any code:

```text
PricingCard
├── Header (plan name, badge "Popular")
├── Price (amount, period)
├── FeatureList (icon + text) ×5
└── CTA Button (primary)
```

Note the layout (flex or grid, alignment, gaps), the states shown (hover, active, disabled, empty, loading), and which content is static and which should be props.

## 3. Build

- **Reuse existing components and tokens.** Map the colors and spacing in the image to the *nearest theme token* rather than hard-coding hex or pixel values.
- Turn dynamic content into typed props with sensible defaults. Repeated items become arrays.
- **Responsive**: mobile first, with breakpoints that follow the project's conventions.
- **Accessible**: semantic elements, labels and alt text, keyboard focus states and sufficient contrast.
- Use the project's icon library. Never embed raster screenshots of icons.
- Use realistic placeholder content, and route strings through i18n if the project uses it.

## 4. Verify

- Type-check and lint. If Storybook exists, add a story with the main states. Otherwise add a usage example.
- If a dev server or browser tool is available, render the component and compare it with the screenshot, then fix spacing and alignment differences.
- Report which existing components were reused, the assumptions you made (exact fonts, unclear interactions) and the pieces the user should confirm.

## Rules

- Don't add new UI libraries or fonts without asking.
- Pixel-perfect matters less than following the design system. If they conflict, follow the system and mention the difference.
