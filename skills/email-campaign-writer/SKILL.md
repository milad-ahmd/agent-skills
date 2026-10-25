---
name: email-campaign-writer
description: Writes email marketing campaigns and automated sequences such as welcome series, product launches, newsletters, abandoned cart, re-engagement, and sales promotions, with subject-line variants, preview text, segmentation, send timing, and deliverability and compliance checks. Use when the user asks for marketing emails, newsletters, drip or nurture sequences, or email subject lines.
---

# Email Campaign Writer

Emails that get opened, read and clicked, and that don't land in spam.

## 1. Brief

Goal (nurture, launch, sale, retention), audience segment and where they are in the journey, the offer, the brand voice, the ESP in use (Mailchimp, Klaviyo, ConvertKit/Kit, Beehiiv, Brevo...) and the merge tags it supports.

## 2. Sequence templates

| Sequence | Emails |
|---|---|
| Welcome | 1 deliver lead magnet + set expectations → 2 story / why us → 3 best content → 4 social proof → 5 soft offer |
| Launch | Teaser → Open cart (value + offer) → Proof / FAQ → Objection handling → Last chance (real deadline only) |
| Abandoned cart | 1h reminder → 24h objections + reviews → 48–72h incentive (optional) |
| Re-engagement | "Still want these?" → best-of → final "we'll remove you" (list hygiene) |
| Newsletter | One main story/insight + 2–3 curated items + one CTA |

For each email, include the send delay or trigger and the goal of that email.

## 3. Each email

- **3 subject lines** of 50 characters or fewer (curiosity, benefit, personal) plus **preview text** of 90 characters or fewer that complements the subject.
- A body with one idea and **one primary CTA**, short paragraphs, written like a person rather than a brochure.
- Personalisation with merge tags (`{{first_name|default:"there"}}`) and their fallbacks.
- A P.S. line, which is often the second most-read part.
- A plain-text version, and a note on rendering in dark mode.

## 4. Deliverability and compliance checklist

A clear sender name, no spammy words in all caps, a sensible link and image ratio, an unsubscribe link, the physical address (CAN-SPAM), consent (GDPR) and authenticated sending (SPF, DKIM, DMARC). Mention these to the user if they aren't set up.

## 5. Test and measure

A/B test one variable (the subject line first). Track open rate (unreliable because of Apple Mail Privacy Protection), **click rate**, conversions and unsubscribes.

## Rules

- No fake urgency, fake "Re:/Fwd:" subjects or deceptive claims.
- Use only the real offers, prices and testimonials that the user provides.
