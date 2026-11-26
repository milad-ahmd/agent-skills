---
name: repo-card
description: Makes a 1280x640 social preview card (Open Graph image) for a repository from its real data, the image X, LinkedIn, Slack and Discord show when the link is shared. A stdlib script reads the name from the git remote, a short tagline, languages by file count, the license and the latest tag, and can add real stars, forks and topics from the GitHub API without a token. It writes an SVG card in clean, terminal or bold templates with light and dark themes, shrinks and wraps long names and taglines, converts it to PNG with rsvg-convert, cairosvg, ImageMagick or headless Chrome when one is installed, and prints og and twitter meta tags for a project site. The user uploads it in the repository settings by hand. Use when the user asks for a social preview image, social card, OG image, Open Graph image, link preview, share image or banner for their repository, wants a nicer preview when the link is pasted into a chat or feed, or needs og:image meta tags for a project site.
---

# Repo Card

When someone pastes a repo link, the preview image is the first thing people see. Without a custom one, GitHub shows a generic card. This skill builds one from facts about the repo and hands the user a PNG to upload.

## What the platforms accept

Each point below was checked against the platform's own docs.

GitHub's social preview takes a PNG, JPG or GIF under 1 MB. It should be at least 640x320, and 1280x640 displays best. Transparent PNGs work, but GitHub suggests a solid background, so every template has one. The upload lives in the repo's Settings page under Social preview. GitHub documents no REST or GraphQL call that sets the image (GraphQL can only read `openGraphImageUrl`), so the user uploads it by hand.

X's `summary_large_image` card wants a 2:1 image between 300x157 and 4096x4096, under 5 MB, in JPG, PNG, WEBP or GIF. It does not support SVG. When `twitter:` tags are missing, X reads the `og:` ones.

Open Graph requires `og:title`, `og:type`, `og:image` and `og:url`. Any page that sets `og:image` should also set `og:image:alt`.

## 1. Agree the tagline

Ask for one short, concrete sentence about what the project does, ideally under 90 characters: "Turns Kubernetes manifests into readable diagrams" rather than "A modern toolkit for developers". Without `--tagline` the script uses the GitHub description, then the first prose line of the README. If neither reads well, suggest a line and ask before rendering.

## 2. Pick a template and render

`clean` (the default) is white or GitHub-dark with an accent stripe. `terminal` draws a window with a `git clone` line in monospace, which suits CLIs. `bold` tints the whole background and makes the name bigger. The accent color comes from the main language.

```bash
python3 scripts/repo_card.py --github owner/name --tagline "Turns Kubernetes manifests into readable diagrams"
python3 scripts/repo_card.py --template terminal --theme dark --out assets/card.svg
python3 scripts/repo_card.py --logo assets/logo.png --owner ""     # logo top right, owner hidden
python3 scripts/repo_card.py --og-html https://owner.github.io/name/  # also print meta tags
python3 scripts/repo_card.py --json                                 # data, outputs, warnings
```

The script writes `card.svg`, then `card.png` beside it if it finds a converter. It tries rsvg-convert first, then the cairosvg module, ImageMagick (`magick`, or `convert` when it really is ImageMagick) and finally Chrome or Chromium with `--headless --screenshot`. It never installs anything. With no converter it prints the exact command to run and says why a PNG is needed. It also checks that the PNG is 1280x640 and under 1 MB.

Exit codes: `0` card written, with any PNG problem printed as a warning. `2` bad input: not a directory, `--github` not in `owner/name` form, a missing or unsupported logo, a missing `--out` folder, or an `--og-html` value that is not an http(s) URL. When the API is offline or rate-limited, `--github` warns and the card falls back to local data with no star or fork badges.

## 3. Look at it

Open the PNG (read the image, or `open card.png`) before handing it over. Feeds usually show it at roughly 500-600px wide. At that size the name must be readable and the tagline must not stop mid-thought. If the name ended in an ellipsis, offer a shorter display name with `--name`. If the first render looks plain, try one more template or theme and let the user choose.

## 4. Tell the user where it goes

For GitHub: Settings, then Social preview, Edit, "Upload an image...", and pick `card.png`. GitHub only shares the image for public repos. For a project site, host the PNG and paste the `--og-html` tags into `<head>`. `og:image` must be an absolute URL, which `--image-url` can set. Platforms cache previews, so a link that was already shared may keep the old image for a while.

## What goes on the card

Only data that exists: owner and name, the tagline, up to three topics, the license, the latest tag, and stars and forks from the API. A count of zero is left off instead of showing "0 stars". The language bar counts files from `git ls-files`. It skips vendored code, `node_modules`, build output and data formats like Markdown, JSON or YAML, so it can differ from GitHub's byte-based bar. No text is smaller than 26px on the 1280px canvas.

## Report

Give the PNG and SVG paths, the template and theme, the tagline used and the facts shown. Then give the upload steps, or the conversion command if no converter was found, and the meta tags if the user has a site.

## Do not

- Upload the image or change repo settings. The user does the upload.
- Invent or round up numbers, or add badges ("build passing", "1k+ users") the repo can't back up.
- Shrink text to fit a long tagline. If it ends in an ellipsis, shorten the tagline.
- Hand over only the SVG for GitHub or X. Neither accepts SVG.
- Put the README on the card. The name and one sentence are enough.
- Install converters or fonts yourself. Print the install or convert command and let the user decide.
