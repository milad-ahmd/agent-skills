# README intro: before and after

The **before** text was written by an LLM for this example, in its default style, as the intro of an open-source library's README (FlowSync is made up). It is not a real person's writing.
The **after** text is the kind of rewrite this skill aims for.

## Before

```text
# 🚀 FlowSync

**FlowSync** is a powerful, lightweight, and blazing-fast library that seamlessly synchronizes your data across multiple platforms. Whether you're a solo developer, a startup, or an enterprise team, FlowSync empowers you to build robust, scalable applications with ease.

## ✨ Why FlowSync?

In today's fast-paced development landscape, keeping data in sync is more crucial than ever. FlowSync isn't just another sync library — it's a complete solution designed to streamline your workflow and unlock the full potential of your data.

- ⚡ **Lightning Fast:** Optimized for performance at every level.
- 🔒 **Secure by Design:** Your data is protected with industry-leading encryption.
- 🧩 **Seamless Integration:** Works with your favorite tools and frameworks out of the box.
- 🌍 **Cross-Platform:** Runs anywhere JavaScript runs.

Let's dive in and see how FlowSync can revolutionize your development experience! 🎉
```

`python3 scripts/slop_scan.py --stdin-name before - < before.md`

```text
before: slop-signal 90/100 (high), 125 words, 9 sentences
  patterns     225.3  weighted hits per 1k words             signal 1.00
  rhythm         0.4  sentence-length CV                     signal 0.74
  paragraphs     n/a  paragraph-length CV                    signal 0.00
  dashes         8.0  em-dashes per 1k words                 signal 0.67
  triads        16.0  'X, Y, and Z' per 1k words             signal 0.80
  lists           67  % lines that are list items/headings   signal 1.00
  18 pattern hits:
    line    7  [in-todays-world] "In today's fast-paced development landscape" -> start with the specific claim
    line    7  [not-just-x-its-y] "isn't just another sync library — it's" -> state Y directly
    line   14  [lets-dive-in] "Let's dive in" -> delete it; start
    line    1  [emoji-line-start] "# 🚀" -> plain bullets and headings
    line    5  [emoji-line-start] "## ✨" -> plain bullets and headings
    line    9  [emoji-line-start] "- ⚡" -> plain bullets and headings
    line   10  [emoji-line-start] "- 🔒" -> plain bullets and headings
    line   11  [emoji-line-start] "- 🧩" -> plain bullets and headings
    line   12  [emoji-line-start] "- 🌍" -> plain bullets and headings
    line    3  [whether-youre] "Whether you're a solo developer, a startup, or an enterprise" -> pick the reader you are writing for
    line    7  [unlock-potential] "unlock the full potential" -> state the concrete result
    line    3  [seamless] "seamlessly" -> say what step it removes
    line   11  [seamless] "Seamless" -> say what step it removes
    line   14  [hype-change] "revolutionize" -> describe the before and after
    line   14  [emoji-inline] "🎉" -> delete it
    line    3  [corporate-buzzwords] "empowers" -> say who can now do what
    line    7  [corporate-buzzwords] "streamline" -> say who can now do what
    line    7  [crucial] "crucial" -> say what breaks without it

Style signals only. A score is not evidence that a person used AI.
```

## After

```text
# FlowSync

FlowSync keeps a local SQLite copy of a Postgres table in sync in both directions, for apps that have to keep working offline. It is a small JavaScript library with no runtime dependencies.

Conflicts are resolved per column, not per row, so two people editing different fields of the same record don't overwrite each other. When both edit the same field, the later write wins and the earlier one is kept in a conflicts table you can inspect.

It does not encrypt anything itself. Use TLS between client and server, and Postgres row-level security to decide who can sync which rows.

Tested on [the runtimes in your CI matrix].
```

```text
after: slop-signal 7/100 (low), 110 words, 7 sentences
  patterns       0.0  weighted hits per 1k words             signal 0.00
  rhythm        0.44  sentence-length CV                     signal 0.64
  paragraphs     0.5  paragraph-length CV                    signal 0.26
  dashes         0.0  em-dashes per 1k words                 signal 0.00
  triads         0.0  'X, Y, and Z' per 1k words             signal 0.00
  lists           20  % lines that are list items/headings   signal 0.33

Style signals only. A score is not evidence that a person used AI.
```

## What changed

Emoji headings, "Whether you're a X, a Y, or a Z", "isn't just X, it's Y" and hype verbs say nothing a user can check. The rewrite says what the library does, how it handles the hard case (conflicts), and what it does *not* do. The technical details stand in for facts the maintainer supplies.
