# Skill trigger evals

Agents decide whether to load a skill from its `description` alone. These evals check two things for every skill in `skills/`:

1. **It triggers** for the prompts it should own.
2. **It stays quiet** for prompts that belong to a neighbouring skill (commit-craft vs changelog-from-git, youtube-script-writer vs youtube-seo-optimizer, and so on).

## Format

One file per skill, `evals/<skill>.json`, where the file name matches the skill folder:

```json
{
  "skill": "commit-craft",
  "should_trigger": [
    "commit this",
    "I've got a pile of unrelated changes in my working tree, split them into clean commits",
    "Write a good commit message for what's staged",
    "Tidy up my history before I open the PR, it's 14 'wip' commits",
    "Use conventional commits for these changes"
  ],
  "should_not_trigger": [
    "Write the changelog for our 3.2 release",
    "Review my branch before I push",
    "Check the repo for leaked API keys before I push"
  ]
}
```

- `should_trigger`: 5 prompts written the way real users type. Keep them varied: short and long, casual and precise, and **at least two that don't reuse the description's wording**. If every prompt copies the description, the evals pass and prove nothing.
- `should_not_trigger`: 3 prompts that a *different* skill in this repo should own. Pick the neighbours a model is most likely to confuse with this skill.

## Run offline (free, CI-safe)

```bash
python3 scripts/run_trigger_evals.py                      # markdown table to stdout
python3 scripts/run_trigger_evals.py -k 1 --skill commit-craft
python3 scripts/run_trigger_evals.py --json results.json --summary "$GITHUB_STEP_SUMMARY" --fail-below 0.8
```

Offline mode is a **lexical router baseline**. It ranks every prompt against every skill that exists in `skills/` at run time, using TF-IDF cosine similarity over the skill's name (weighted ×2), its description and its "Use when…" clause (counted again). Tokens are lowercased words of 2+ characters, minus the skills-doctor stopwords and common chat filler, with crude suffix stemming.

It reports, per skill:

| Column | Meaning |
|---|---|
| `hit@k` | should_trigger prompts where the skill ranks in the top k (default 3) |
| `top-1` | should_trigger prompts where the skill ranks first |
| `confused` | should_not_trigger prompts where the skill still ranks first |
| `flag` | `LOW` when hit@k is below `--threshold` (default 0.6), `CONFUSED` when anything was confused |

It also lists every miss and confusion, eval files whose skill no longer exists, skills with no eval file, and malformed files. It always exits 0 unless you pass `--fail-below X`, which fails when the overall hit@k rate is below X.

> **Caveat:** offline mode is a proxy. It measures how *lexically* distinctive the descriptions are, not what a model actually loads. A model understands "Users keep asking what's new" means release notes; a word-overlap router doesn't. Treat an offline miss as "this prompt shares few words with the description", which is worth a look but isn't proof of a bug. A confusion is a stronger signal: two descriptions share the words that matter. Only live mode tells you what the model does.

## Run live (spends model usage, never in CI)

```bash
python3 scripts/run_trigger_evals.py --live                                   # prints the session count, runs nothing
python3 scripts/run_trigger_evals.py --live --skill commit-craft --yes        # 8 sessions: try one skill first
python3 scripts/run_trigger_evals.py --live --model sonnet --jobs 4 --yes     # everything, report to evals/RESULTS.md
```

For each prompt, live mode creates a temp project with **every** skill copied into `.claude/skills/`, sends the prompt on stdin to `claude -p --output-format stream-json --verbose`, and looks for a `Skill` tool_use in the stream. Each session is locked down so that loading a skill is the only thing that can happen:

- `--tools Skill --allowed-tools Skill` plus `--disallowed-tools Bash,Edit,Write,…`, with `--permission-mode dontAsk`
- `--setting-sources project` and `--strict-mcp-config`, meant to keep your user-level skills, plugins and MCP servers out. If something from outside this repo still loads, the report names it
- `--max-turns 2` (change with `--max-turns`), `--no-session-persistence`, and the session is stopped as soon as a Skill call appears

Scoring: a should_trigger prompt is a **hit** if the skill was loaded; a should_not_trigger prompt is a **false trigger** if it was. The report also shows which skill (if any) loaded instead, errors (not logged in, bad flags, timeouts), and the cost Claude Code reports. Options: `--skill NAME` (repeatable), `--model`, `--jobs N` (default 4), `--max-budget-usd` (per session), `--timeout`, `--claude PATH`, `--out evals/RESULTS.md`, `--json FILE`.

Without `--yes` it only prints how many sessions it would start: one per prompt, so 8 for a standard eval file.

## Add evals for a new skill

1. Read the new skill's `SKILL.md` and its neighbours' descriptions.
2. Create `evals/<skill>.json` with 5 `should_trigger` and 3 `should_not_trigger` prompts as described above. Then add the new skill as a likely confuser to one or two neighbours' `should_not_trigger` lists.
3. Run `python3 scripts/run_trigger_evals.py --skill <skill>` and read the misses and confusions. If a neighbour wins on prompts the new skill should own, sharpen the trigger phrases in the description; don't tune the prompts to fit.
4. Optional: `--live --skill <skill> --yes` (8 sessions) to check against the real model.

Tests for the runner: `python3 -m unittest discover -s scripts -p 'test_run_trigger_evals.py'`. They use a synthetic stream-json sample and a fake `claude` script, so they never call the model.
