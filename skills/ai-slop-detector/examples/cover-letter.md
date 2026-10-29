# Cover-letter paragraph: before and after

The **before** text was written by an LLM for this example, in its default style, as a cover letter for a backend engineering job. It is not a real person's writing.
The **after** text is the kind of rewrite this skill aims for.

## Before

```text
Dear Hiring Manager,

I am writing to express my strong interest in the Senior Backend Engineer position at Initech. With over eight years of experience in software development and a proven track record of delivering robust, scalable solutions, I am confident that I would be a valuable addition to your team.

Throughout my career, I have had the opportunity to work on a wide range of challenging projects, leveraging cutting-edge technologies to drive meaningful results. I am particularly passionate about building high-performance systems that empower businesses to thrive in today's competitive landscape. My ability to collaborate effectively with cross-functional teams, combined with my meticulous attention to detail, has consistently enabled me to exceed expectations.

What excites me most about Initech is your commitment to innovation and excellence. I am eager to bring my unique blend of technical expertise, leadership, and passion to help drive your mission forward.

Thank you for considering my application. I look forward to the opportunity to discuss how I can contribute to your team's continued success.
```

`python3 scripts/slop_scan.py --stdin-name before - < before.md`

```text
before: slop-signal 65/100 (high), 170 words, 10 sentences
  patterns      42.6  weighted hits per 1k words             signal 0.82
  rhythm        0.47  sentence-length CV                     signal 0.57
  paragraphs    0.36  paragraph-length CV                    signal 0.59
  dashes         0.0  em-dashes per 1k words                 signal 0.00
  triads         5.9  'X, Y, and Z' per 1k words             signal 0.29
  lists            0  % lines that are list items/headings   signal 0.00
  6 pattern hits:
    line    5  [in-todays-world] "in today's competitive landscape" -> start with the specific claim
    line    5  [wide-range] "a wide range of" -> list two or three, or give the number
    line    5  [cutting-edge] "cutting-edge" -> new; name the technique
    line    5  [meticulous] "meticulous" -> careful; show the care
    line    5  [corporate-buzzwords] "empower" -> say who can now do what
    line    5  [leverage] "leveraging" -> use

Style signals only. A score is not evidence that a person used AI.
```

## After

```text
Dear Ms. Lambert,

I'm applying for the Senior Backend Engineer role on Initech's billing team. For the last three years I've run the invoicing service at [current company], which sends about [N] invoices a month from Go and Postgres. Last year I replaced its nightly batch job with an event-driven pipeline, and the time from usage to invoice dropped from [before] to [after].

Your posting mentions splitting billing out of the monolith without losing a cent. That is the part of the job I'd most like to talk about. I've done one of those migrations, double-charged a few hundred customers during the cutover, and learned exactly which reconciliation checks would have caught it.

I'm based in [city] and can start in [month].
```

```text
after: slop-signal 1/100 (low), 122 words, 9 sentences
  patterns       0.0  weighted hits per 1k words             signal 0.00
  rhythm        0.62  sentence-length CV                     signal 0.19
  paragraphs     n/a  paragraph-length CV                    signal 0.00
  dashes         0.0  em-dashes per 1k words                 signal 0.00
  triads         0.0  'X, Y, and Z' per 1k words             signal 0.00
  lists            0  % lines that are list items/headings   signal 0.00

Style signals only. A score is not evidence that a person used AI.
```

## What changed

The "before" could be sent to any company for any job. The rewrite names the team, one result (numbers left as `[placeholders]`, never invented) and one honest story. The author fills in the brackets before sending.
