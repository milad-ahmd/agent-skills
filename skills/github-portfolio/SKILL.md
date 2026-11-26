---
name: github-portfolio
description: Generates a one-page developer portfolio website from a person's real public GitHub data and gets it ready to publish on GitHub Pages at USERNAME.github.io. A stdlib script fetches the profile, public repos, README intros and language totals without a token (it counts requests to stay inside the 60 per hour limit), picks featured projects by stars and recency, and writes a static index.html with no frameworks, light and dark themes and accessible markup, plus a data.json the user edits to reword or hide projects. The agent then rewrites each project card as problem, what was built and result, using only numbers from the README or the user, and prints the git commands to publish without pushing anything. Use when the user asks for a portfolio, a personal website built from their GitHub repos, a developer one-pager, a showcase of their best projects, a github.io page, or a portfolio link for their CV or job applications.
---

# GitHub Portfolio

A portfolio page is read in under a minute by a recruiter or hiring manager. It should show 3 to 6 real projects, say what each one does and what came of it, and link to the code and the live demo. Everything on it must be true and checkable on GitHub.

## 1. Fetch the public data

```bash
python3 scripts/portfolio.py --user USERNAME --out site --save-raw site-raw.json
python3 scripts/portfolio.py --from-json site-raw.json --out site      # rebuild offline later
```

The script reads only public data from the GitHub REST API. Without a token GitHub allows 60 requests per hour per IP address; the script uses about 2 + 2 per featured project, caps itself at 40, and if the limit or budget runs out it keeps what it has and says the README or language data is incomplete. If `GITHUB_TOKEN` is set it is sent; nothing else reads it. Forks, archived repos, repos without a description, the profile README repo and the old `USERNAME.github.io` repo are never featured.

Exit codes: `0` site written · `2` on any failure (unknown user, API error, unreadable file).

## 2. Choose the projects with the user

Show the summary: the featured list, the other candidates, the languages. Ask which 3 to 6 projects they want and in what order. Good picks are ones they can talk about in an interview and that match the jobs they want. Do not add a fork or a private repo unless the user asks for it by name.

## 3. Rewrite each card honestly

Edit `site/data.json`. For each featured project set `title` (a readable name) and `blurb`, two or three sentences in this order. Start with the problem: what was slow or missing, and for whom. Then say what was built and how, in plain words. End with the result, such as a user count, requests per second or a company that runs it.

Take facts from `readme_excerpt`, the repo's README and the user's answers. If there is no number, ask for one. If the user has none, write the result without a number ("runs the nightly billing jobs at my last team") rather than inventing one. Keep `stack` to the 2 to 4 technologies that matter. Set `live` only to a URL that loads. Set `featured` to `false` to hide a project, and move entries to reorder them. Profile fields (`name`, `bio`, `location`, `website`, `email`) can be edited the same way.

## 4. Generate and check

```bash
python3 scripts/portfolio.py --user USERNAME --out site --overrides site/data.json
```

Re-running with `--overrides` keeps the user's edits. Star counts and dates are refreshed. Repos that are gone or now private are dropped; new repos are added as not featured. Use `--from-json` instead of `--user` to avoid the API.

Open `site/index.html` in a browser if one is available: check the light and dark themes, a 375px wide window, keyboard focus on every link, and that no card text overflows. Without a browser, say that the visual check was not done.

## 5. Publish only when the user says so

```bash
python3 scripts/portfolio.py --publish-instructions --user USERNAME --out site
```

This prints the commands for the `username.github.io` repository (lowercase, as GitHub requires): create the repo, `git init`, commit `index.html`, `data.json` and `.nojekyll`, push to `main`, then set Settings > Pages > Deploy from a branch, `main`, `/(root)`. GitHub says a push can take up to 10 minutes to appear. Give the commands to the user. If they ask you to run them, confirm the repository name first, and if `username.github.io` already has a site, say that the push replaces its home page.

## Output

Give the path to `site/index.html` and the featured projects with their final blurbs. Then list what was not verified, for example the browser check, numbers the user still has to confirm, or README data missing after a rate limit. Add the publish commands when the user asked for them.

## Do not

- Invent metrics or results, or round a number up. Every claim on the page must come from GitHub or the user.
- Feature forks or private repos the user did not pick.
- Run `git push`, create the repository or change GitHub Pages settings unless the user asked for it in this conversation.
- Add trackers or scripts the user did not ask for.
- Put the user's email or phone number on the page unless it is already public on their profile or they gave it for this.
