# Sources

Every third-party rule `scripts/readme_audit.py` relies on, with where it comes from. Add a row here before adding a rule to the script.

## Which README GitHub shows

GitHub Docs, "About the repository README file": https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes

- GitHub looks in `.github/`, then the repository root, then `docs/`, and shows the first README it finds in that order.
- Content past 500 KiB is truncated.
- GitHub builds its own outline (table of contents) from the headings.
- The order between two README files in one directory (README.md vs README.rst) is not documented. The script prefers Markdown and lists the others.

## Relative links and images

GitHub Docs, "Basic writing and formatting syntax", Relative links: https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#relative-links

- Paths are relative to the current file, and a leading `/` means the repository root. `./` and `../` work.
- The docs say link text should stay on one line, so the script reads links line by line.
- GitHub paths are case-sensitive because git stores paths as given. A link to `Docs/Guide.md` is broken when the file is `docs/guide.md`, even if macOS or Windows opens it locally.

## Heading anchors

Official rules: GitHub Docs, same page, "Section links": https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#section-links

- Letters are converted to lower case. Spaces become `-`. Other whitespace and punctuation are removed. Markup is removed and its contents kept. A duplicate gets `-1`, `-2` and so on.

Those rules leave emoji, symbols and repeated duplicates open. Two implementation references were checked against GitHub's renderer:

- html-pipeline `TableOfContentsFilter` (extracted from GitHub): https://github.com/gjtorikian/html-pipeline/blob/v2.14.3/lib/html/pipeline/toc_filter.rb
  - It removes `[^\p{Word}\- ]`, and Ruby's `\p{Word}` keeps letters, marks, decimal digits, connector punctuation and the join controls.
  - Then it turns spaces into `-`.
- github-slugger: https://github.com/Flet/github-slugger (index.js, script/generate-regex.js, test/fixtures.json)
  - It removes symbols, most punctuation, `Other_Number` and separators except the space, and keeps `Alphabetic`.
  - Its duplicate loop turns `A, A, A-1, A` into `a, a-1, a-1-1, a-2`.

The script was checked against GitHub's own renderer (`POST https://api.github.com/markdown` with mode `markdown`, which returns the heading ids). All 35 edge-case headings matched (test_slug_matches_github holds 16 of them), and so did all 208 heading ids of six public READMEs (express, fastapi, lazygit, bubbletea, httpie, bat) fetched as GitHub-rendered HTML. One fix came from this check: the text of a multi-line HTML heading keeps its inner whitespace. The facts it settled:

- The emoji is dropped, but a following U+FE0F variation selector (a mark) and a zero-width joiner stay in the id, so `## ⚙️ Configuration` gets the id `️-configuration`, not `-configuration`.
- Lower-casing is Unicode and per character: `ΣΑΣ` becomes `σασ`.
- `:rocket:` shortcodes are slugged from the source text, so the id is `rocket`.
- Duplicates follow the github-slugger loop.
- A leading `<img>` leaves its trailing space, so the id becomes `-logo`.
- `²`, `½` and other `Other_Number` characters are removed.

`#top` and an empty `#` scroll to the top of the page: WHATWG HTML, "scroll to the fragment": https://html.spec.whatwg.org/multipage/browsing-the-web.html#scroll-to-the-fragment-identifier

## Commands in install sections

- npm ci: "The project must have an existing package-lock.json or npm-shrinkwrap.json." https://docs.npmjs.com/cli/v10/commands/npm-ci
- npm run-script: a missing script is an error unless `--if-present` is set. https://docs.npmjs.com/cli/v10/commands/npm-run-script
- pip, local directories: pip refuses a directory with neither `setup.py` nor `pyproject.toml` ("Directory ... is not installable"). https://github.com/pypa/pip/blob/main/src/pip/_internal/req/constructors.py
- Python name comparison: `re.sub(r"[-_.]+", "-", name).lower()`, from the Python Packaging User Guide, "Names and normalization". https://packaging.python.org/en/latest/specifications/name-normalization/
- Python project name sources: `[project] name` (pyproject.toml specification, https://packaging.python.org/en/latest/specifications/pyproject-toml/), Poetry's `[tool.poetry] name`, `setup.cfg [metadata] name` and `setup(name=...)`.
- Docker Compose default files: `compose.yaml` (preferred) or `compose.yml`, with `docker-compose.yaml` and `docker-compose.yml` for backwards compatibility. https://docs.docker.com/compose/intro/compose-application-model/
- docker build: `-f, --file` "Name of the Dockerfile (default: PATH/Dockerfile)", where PATH is the build context. https://docs.docker.com/reference/cli/docker/buildx/build/
- GNU make tries `GNUmakefile`, `makefile`, then `Makefile`. https://www.gnu.org/software/make/manual/html_node/Makefile-Names.html
- Cargo looks for `Cargo.toml` "in the current directory or any parent directory". https://doc.rust-lang.org/cargo/commands/cargo-build.html (`--manifest-path`)
- Go finds the main module by looking for `go.mod` in the current directory and then its parents. https://go.dev/ref/mod#go-mod-file

## License files

GitHub Docs, "Licensing a repository": the license text usually lives in `LICENSE`, `LICENSE.txt`, `LICENSE.md` or `LICENSE.rst` in the root, and the Licensee gem compares it with known licenses. https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository

The script also accepts `LICENCE*`, `COPYING*` and `UNLICENSE` in the root (for example LICENSE-MIT plus LICENSE-APACHE for a dual license). These are common names, but this file has not checked whether Licensee reads them.

The script compares license families (MIT, Apache, GPL, LGPL, AGPL, BSD, MPL, ISC, Unlicense, Creative Commons). It uses key phrases from each license's standard text, not full license matching like GitHub's licensee, so a mismatch is reported with medium confidence.
