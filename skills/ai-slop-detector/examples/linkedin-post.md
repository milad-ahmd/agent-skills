# LinkedIn post: before and after

The **before** text was written by an LLM for this example, in its default style, as a job-change announcement. It is not a real person's writing.
The **after** text is the kind of rewrite this skill aims for.

## Before

```text
🚀 Excited to share some big news! 🚀

After 3 incredible years at Acme, I'm thrilled to announce that I'm embarking on a new chapter as a Senior Engineer at Globex! 🎉

This isn't just a new job. It's a chance to grow, learn, and make a real impact.

Here's what I learned along the way:

👉 **Embrace the uncertainty:** Growth happens outside your comfort zone.
👉 **Relationships matter:** The people you work with make all the difference.
👉 **Never stop learning:** In today's fast-paced tech landscape, curiosity is key.

I'm deeply grateful to my incredible team at Acme for their unwavering support. You've truly shaped who I am today.

The future is bright, and I can't wait to see what's next! 💡

What's the biggest lesson you've learned from a career change? Drop your thoughts in the comments! 👇

#CareerGrowth #NewBeginnings #Leadership #Engineering
```

`python3 scripts/slop_scan.py --stdin-name before - < before.md`

```text
before: slop-signal 79/100 (high), 136 words, 14 sentences
  patterns     236.7  weighted hits per 1k words             signal 1.00
  rhythm        0.47  sentence-length CV                     signal 0.58
  paragraphs     0.3  paragraph-length CV                    signal 0.74
  dashes         0.0  em-dashes per 1k words                 signal 0.00
  triads         7.4  'X, Y, and Z' per 1k words             signal 0.37
  lists            0  % lines that are list items/headings   signal 0.00
  18 pattern hits:
    line    5  [not-just-x-its-y] "isn't just a new job. It's" -> state Y directly
    line   11  [in-todays-world] "In today's fast-paced tech landscape" -> start with the specific claim
    line   15  [future-is-bright] "The future is bright" -> end on a concrete next step or open question
    line   17  [engagement-bait] "Drop your thoughts in" -> ask a real, specific question or end
    line   19  [hashtag-block] "#CareerGrowth #NewBeginnings #Leadership #Engineering" -> one or none
    line    1  [emoji-line-start] "🚀" -> plain bullets and headings
    line    9  [emoji-line-start] "👉" -> plain bullets and headings
    line   10  [emoji-line-start] "👉" -> plain bullets and headings
    line   11  [emoji-line-start] "👉" -> plain bullets and headings
    line   11  [is-key] "is key." -> say what to do
    line    9  [embrace-the] "Embrace the" -> accept, use; or say what to do
    line   10  [make-a-difference] "make all the difference" -> say what changes
    line    1  [emoji-inline] "🚀" -> delete it
    line    3  [embark] "embarking on" -> start, begin
    line    3  [emoji-inline] "🎉" -> delete it
    line   15  [emoji-inline] "💡" -> delete it
    line   17  [emoji-inline] "👇" -> delete it
    line   13  [truly] "truly" -> delete it

Style signals only. A score is not evidence that a person used AI.
```

## After

```text
Some news: on 3 March I start at Globex as a senior engineer on the payments team.

I spent three years at Acme. What I'll miss most is the Thursday incident review, where anyone could bring a bug they had caused and walk us through it without being blamed. I learned more in that room than in any course. Thank you, [name], for [the specific thing they did].

At Globex I'll be working on [the actual project]. It's the reason I said yes.

If you've moved from a product team to a platform team, I'd like to hear what surprised you in the first month.
```

```text
after: slop-signal 2/100 (low), 105 words, 8 sentences
  patterns       0.0  weighted hits per 1k words             signal 0.00
  rhythm        0.54  sentence-length CV                     signal 0.40
  paragraphs    0.55  paragraph-length CV                    signal 0.12
  dashes         0.0  em-dashes per 1k words                 signal 0.00
  triads         0.0  'X, Y, and Z' per 1k words             signal 0.00
  lists            0  % lines that are list items/headings   signal 0.00

Style signals only. A score is not evidence that a person used AI.
```

## What changed

Engagement bait, emoji bullets, bold lead-ins and a hashtag block carry most of the score. The rewrite keeps the news and the gratitude, adds the specifics only the author knows (left as `[placeholders]` where they're missing), and ends on a question someone could actually answer.
