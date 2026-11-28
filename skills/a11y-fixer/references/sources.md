# Sources

Every rule in `scripts/a11y_scan.py` and the WCAG criterion it reports, with the official source it was checked against (w3.org, November 2026). Add a row here before adding a rule to the script.

## Success criteria (WCAG 2.2, https://www.w3.org/TR/WCAG22/)

All the criteria the scan reports are Level A.

| SC | Name | URL |
|---|---|---|
| 1.1.1 | Non-text Content | https://www.w3.org/TR/WCAG22/#non-text-content |
| 1.3.1 | Info and Relationships | https://www.w3.org/TR/WCAG22/#info-and-relationships |
| 1.4.2 | Audio Control | https://www.w3.org/TR/WCAG22/#audio-control |
| 2.1.1 | Keyboard | https://www.w3.org/TR/WCAG22/#keyboard |
| 2.4.3 | Focus Order | https://www.w3.org/TR/WCAG22/#focus-order |
| 2.4.4 | Link Purpose (In Context) | https://www.w3.org/TR/WCAG22/#link-purpose-in-context |
| 3.1.1 | Language of Page | https://www.w3.org/TR/WCAG22/#language-of-page |
| 3.3.2 | Labels or Instructions | https://www.w3.org/TR/WCAG22/#labels-or-instructions |
| 4.1.2 | Name, Role, Value | https://www.w3.org/TR/WCAG22/#name-role-value |

## Rules

Techniques live under `https://www.w3.org/WAI/WCAG22/Techniques/` (failures/F.., html/H.., general/G..). ACT rules live under `https://www.w3.org/WAI/standards-guidelines/act/rules/<id>/`.

| Rule | SC | Source and what it says |
|---|---|---|
| img-alt | 1.1.1 | F65: failure of 1.1.1 when the alt attribute or text alternative is missing on img, area and input type=image. ACT 23a2a8 "Image has non-empty accessible name" (decorative images marked with alt="" or role none/presentation pass). |
| alt-not-alternative | 1.1.1 | F30: filenames ("Oct.jpg"), placeholder text ("spacer", "image", "picture") and programming references ("picture 1", "0001") are not text alternatives. |
| input-image-alt | 1.1.1 | F65 and H36 (alt on image submit buttons). |
| area-alt | 1.1.1, 2.4.4 | F65 and H24 (text alternatives for area elements; relates to 1.1.1 and 2.4.4). |
| control-label | 4.1.2, 3.3.2 | F68: failure of 4.1.2 when a control has no programmatically determined name. ACT e086e5 "Form field has non-empty accessible name". H44 (label elements; relates to 1.1.1, 1.3.1, 3.3.2, 4.1.2). |
| button-name | 4.1.2 | ACT 97a4e1 "Button has non-empty accessible name". |
| link-name | 2.4.4, 4.1.2 | ACT c487ae "Link has non-empty accessible name" (4.1.2, 2.4.4, 2.4.9). F89: failure of 2.4.4, 2.4.9 and 4.1.2 when an image is the only content of a link and has no accessible name. |
| html-lang | 3.1.1 | H57 (lang on the html element). ACT b5c3f8 "HTML page has lang attribute". |
| iframe-title | 4.1.2 | H64 (title on iframe; relates to 2.4.1 and 4.1.2). ACT cae760 "Iframe element has non-empty accessible name" (status: proposed). |
| tabindex-positive | 2.4.3 | F44: failure of 2.4.3 when tabindex creates a tab order that does not preserve meaning and operability. A positive value is a failure only when the order it makes is wrong, so the scan reports medium. |
| media-autoplay | 1.4.2 | F93: failure when an audio or video element with an audio track has autoplay, no muted attribute and no control to pause or stop it. The scan cannot know whether there is an audio track or whether it lasts over 3 seconds, so it reports medium and skips elements with muted or controls. ACT 80f0bf (proposed). |
| click-no-keyboard | 2.1.1, 4.1.2 | F54: failure of 2.1.1 when a function uses only pointing-device event handlers. F42: failure of 1.3.1, 2.1.1, 2.1.3 or 4.1.2 when emulating links with non-link elements. |
| aria-hidden-focus | 4.1.2 | ACT 6cfa84 "Element with aria-hidden has no content in sequential focus navigation" (tests 4.1.2). Its passed examples include a disabled input and a button with tabindex="-1", so the scan skips both. |
| role-invalid | 4.1.2 | ACT 674b10 "Role attribute has valid value": at least one token must be a non-abstract role from the WAI-ARIA specifications. ACT maps it to techniques ARIA4 and G108 (both for 4.1.2) and states it is "not required for conformance", so the scan reports medium. |
| heading-skip | 1.3.1 | G141 "Organizing a page using headings" is Advisory for 1.3.1 and says headings "should" be properly nested (h2 after h1, h3 after h2). Skipping a level is not a WCAG failure on its own, so the scan always reports low. |

## Accessible names (HTML-AAM, https://www.w3.org/TR/html-aam-1.0/)

The scan treats an element as named when the HTML-AAM computation could give it a name without running code:

- input (text-like types) and textarea: aria-labelledby, aria-label, associated label element, title, then placeholder.
- button: aria-labelledby, aria-label, associated label, subtree text, then title.
- a: aria-labelledby, aria-label, subtree text, then title.
- area: aria-labelledby, aria-label, alt, then title.
- iframe: aria-labelledby, aria-label, then title.
- Content inside an element with aria-hidden="true" is left out of the subtree text (WAI-ARIA 1.2, aria-hidden state; accessibility tree exclusion).

input type=submit and type=reset: when there is no value, HTML-AAM uses an implementation-defined string such as "Submit", so they are never reported. input type=button has no such default, so an empty one is reported.

## ARIA roles

- WAI-ARIA 1.2 (https://www.w3.org/TR/wai-aria-1.2/): the 82 concrete role names in `ROLES`. Abstract roles (command, composite, input, landmark, range, roletype, section, sectionhead, select, structure, widget, window): "Authors MUST NOT use abstract roles in content."
- WAI-ARIA 1.3 Editor's Draft (https://w3c.github.io/aria/): adds comment, image, mark, sectionfooter, sectionheader, suggestion. It is a draft; the scan accepts these names so it never reports a role browsers are adding.
- DPUB-ARIA 1.1 (https://www.w3.org/TR/dpub-aria-1.1/): the 41 doc-* roles.
- Graphics-ARIA 1.0 (https://www.w3.org/TR/graphics-aria-1.0/): graphics-document, graphics-object, graphics-symbol.

## Component syntax

- React DOM common components (https://react.dev/reference/react-dom/components/common): htmlFor, className, tabIndex, dangerouslySetInnerHTML; event props are camelCase (onClick, onKeyDown).
- Vue template syntax and directives (https://vuejs.org/guide/essentials/template-syntax.html, https://vuejs.org/api/built-in-directives.html, https://vuejs.org/guide/essentials/event-handling.html): `:x` is v-bind, `@x` is v-on, `.stop`-style modifiers, v-bind and v-on without an argument take an object, `:[key]` is a dynamic name, v-html and v-text replace content.
- Svelte markup (https://svelte.dev/docs/svelte/basic-markup, https://svelte.dev/docs/svelte/legacy-on): capitalised or dotted tags are components, `{name}` is shorthand for name={name}, `{...props}` spreads, Svelte 5 uses onclick, Svelte 4 on:click with `|` modifiers.
- Angular `[x]`, `[attr.x]` and `(event)` bindings and Alpine `x-bind:`/`x-on:` in .html files are treated the same way as Vue's. These were not checked against the Angular or Alpine docs; they only make the scan more cautious (a value it cannot see counts as present).

## Not covered

Colour contrast (1.4.3, 1.4.11), focus visible (2.4.7), focus not obscured (2.4.11), target size (2.5.8), keyboard traps (2.1.2), captions (1.2.x) and reflow (1.4.10) need computed styles or a running page. Use axe-core or Lighthouse on the rendered page for those.
