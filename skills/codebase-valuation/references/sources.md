# Sources

Every constant and default in `scripts/valuation.py`, with where it comes from. Add a row here before changing one in the script.

## The model

| Value in the script | Source |
|---|---|
| Basic COCOMO, organic mode: effort = 2.4 x KLOC^1.05 person-months | Barry W. Boehm, *Software Engineering Economics*, Prentice-Hall, 1981 (the book that introduced COCOMO). The organic-mode coefficients are repeated in David A. Wheeler's SLOCCount documentation, [dwheeler.com/sloccount/sloccount.html](https://dwheeler.com/sloccount/sloccount.html): "Basic COCOMO model, Person-Months = 2.4 * (KSLOC**1.05)" |
| schedule = 2.5 x effort^0.38 months | Same SLOCCount page: "Basic COCOMO model, Months = 2.5 * (person-months**0.38)" |
| people = effort / schedule | [Wikipedia, "COCOMO"](https://en.wikipedia.org/wiki/COCOMO): average staffing P = E / D |
| The same four numbers in scc | scc source, [processor/cocomo.go](https://github.com/boyter/scc/blob/master/processor/cocomo.go): `"organic": {2.4, 1.05, 2.5, 0.38}`, effort = a x (SLOC / 1000)^b x EAF, schedule = c x effort^d, cost = effort x averageWage / 12 x overhead. The scc README shows the same set as `--cocomo-project-type "organic,2.4,1.05,2.5,0.38"` |
| cost = effort / 12 x salary x overhead | scc `EstimateCost` (above). SLOCCount computes the same: person-years x salary x overhead |
| Default salary 56,286 (USD a year) | SLOCCount page: "The default annual salary is $56,286 per year", from a ComputerWorld salary survey of the average US programmer/analyst, 4 September 2000. scc's `--avg-wage` default is the same 56286 ([scc README](https://github.com/boyter/scc#readme)) |
| Default overhead 2.4 | SLOCCount page: no definitive source was found; after informal talks with cost analysts, 2.4 was taken as representative of a typical software company's overhead. scc's `--overhead` default is 2.4, described as covering facilities, equipment, accounting and so on |
| KLOC counts code lines only | SLOCCount defines physical SLOC as a line with at least one non-whitespace, non-comment character, so comments and blank lines are left out |
| Blank lines inside a block comment count as comment lines | scc README: "Blank lines inside comments are counted as comments" |

Not verified: Boehm's book was not read directly; the coefficients above come from SLOCCount and scc, which both label them basic COCOMO. Which of scc's line counts feeds its COCOMO estimate was not checked in its source, so the script follows SLOCCount's definition.

The default salary is a year-2000 US figure and the script does not adjust it for inflation or location. Pass `--salary` for a current or local number.

## Skipping and counting rules

- Generated Go files carry a line matching `^// Code generated .* DO NOT EDIT\.$` before the first non-comment, non-blank text (Go command documentation, [`go generate`](https://pkg.go.dev/cmd/go#hdr-Generate_Go_files_by_processing_source)). The script looks for "Code generated" and "DO NOT EDIT" in the first five lines of any language, and also skips files with an `@generated` marker there. The `@generated` check is a heuristic, not a standard.
- Lockfile names and vendored directory names start from the lists in `skills/bus-factor`, plus common build and cache directories (`.venv`, `venv`, `target`, `coverage`, `.next`, `.nuxt`, `__pycache__`). Docs and data files (Markdown, JSON, YAML, TOML, CSV and similar) are not counted as code.
- Minified JavaScript and CSS are detected by name (`*.min.js`, `*.min.css`) or an average line length over 500 characters. The 500 is a judgement call, not a sourced figure.
- Strings are recognised within one line only. Comment markers inside multi-line strings (Python triple quotes, JS template literals) can be misread, and Python docstrings count as code.
