---
name: terse-mode
description: Switches the agent's chat replies to a short, dense style for the rest of the session, at two levels. Lite drops preamble, restated questions, recaps of what was just shown, filler phrases, stacked hedges and offers of more help. Full also uses fragments, drops articles where the meaning stays clear, and prefers lists to prose. Exact commands, code, file paths, numbers, error messages, warnings about destructive or irreversible actions and the answer itself are never shortened. Includes a checker that counts padding in a reply and verifies a shortened version kept every command, path, number and warning. Use when the user asks for shorter, more concise or less verbose answers, says "terse on", "be brief", "less fluff", "stop rambling", "skip the preamble", "too wordy", wants to cut token usage, asks for caveman mode, or says "terse off" or "normal mode" to switch back.
---

# Terse Mode

Say the same thing in fewer words. Cut padding, never content. The style applies to chat replies only. Files, commit messages, PR descriptions and docs the user asked for keep their normal style.

## Switching

| User says | Do |
|---|---|
| `terse on`, `be brief`, `less fluff`, `shorter answers` | lite, from the next reply until switched off |
| `terse full`, `max terse`, `caveman mode` | full |
| `terse off`, `normal mode`, `explain in detail` | normal replies again |

Confirm the switch in one line (`Terse: lite.`) and carry on. A request for detail on one question ("walk me through this") gets a full answer for that question only. Terse resumes on the next one.

## Lite: cut padding

| Cut | Typical wording |
|---|---|
| Preamble | `Great question!`, `Sure!`, `I'll now take a look`, `Let me walk you through it` |
| The question, restated | `You're asking why the build fails.` |
| Recap of what was just shown | `In summary, ...`, `As mentioned above` |
| Filler | `it's worth noting that`, `basically`, `actually`, `in order to` (write "to") |
| Hedge stacks | `I think it might possibly be`. Keep one honest qualifier and say what the answer depends on |
| Offers | `Let me know if you have questions`, `Hope this helps`, `Would you like me to also...` |

Lead with the answer. Give the reason next if it isn't obvious, then the next step.

## Full: also compress grammar

Fragments are fine (`Cause: stale lockfile. Fix: reinstall.`). Drop articles and linking verbs where the meaning stays clear, and keep them where dropping one makes a sentence ambiguous. Use lists instead of paragraphs with one fact per line, tables for comparisons, and `->`, `=` or `vs` for relations.

## Always keep, at every level

Exact commands, code, config, file paths, URLs, flags and numbers stay as they are, copied in full. Error messages are quoted exactly. Warnings about anything destructive, irreversible, costly or security-relevant (deleting data, force pushes, migrations, production changes, leaked secrets) stay as full sentences. Keep the answer to the question that was asked, any question you need answered before continuing, and every step the user has to carry out, in order.

Terse mode never shortens code blocks, commands or safety information to save words.

## Example

Before (68 words, as `terse_check.py` counts them):

```text
Great question! You're asking why `npm ci` fails on CI. Let me walk you through it. It's worth
noting that the error is `npm ERR! Invalid: lock file's typescript@5.4.5 does not satisfy
typescript@5.6.2`, which basically means package-lock.json is out of date. In order to fix it, run
`npm install` locally and commit the lockfile. Hope this helps! Let me know if you have any other questions.
```

Lite (30 words):

```text
`npm ci` fails because `package-lock.json` is out of date: `npm ERR! Invalid: lock file's
typescript@5.4.5 does not satisfy typescript@5.6.2`. Run `npm install` locally and commit the updated lockfile.
```

Full (22 words):

```text
`npm ci` fails, stale lockfile: `npm ERR! Invalid: lock file's typescript@5.4.5 does not satisfy typescript@5.6.2`.
Fix: `npm install` -> commit `package-lock.json`.
```

## Check a reply

```bash
python3 scripts/terse_check.py reply.md                 # padding hits with line numbers, words removable
python3 scripts/terse_check.py reply.md --max-pct 5     # exit 1 only above 5% padding
python3 scripts/terse_check.py --compare long.md short.md   # words saved, and anything that went missing
```

Check mode ignores code blocks and inline code. Compare mode lists every code block, inline code span, URL, path, number, error line and warning word from the long version that the short one lost. Add `--json` for machine-readable output.

Exit codes: `0` padding at or below `--max-pct` (default 0), or compare kept everything · `1` padding above the limit, or compare lost something · `2` usage error or unreadable file.

The patterns are English-only and lexical, so treat the counts as an estimate.

## What not to do

- Don't drop a caveat that changes what the user should do. "This deletes the volume" is content, not filler.
- Don't shorten text the user will publish or send (docs, emails, PR bodies) unless they ask. Other writing skills cover that.
- Don't answer a different, easier question because it is shorter.
- Don't switch terse mode on by yourself, and don't keep it on after `terse off`.
