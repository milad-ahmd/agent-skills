# Telegram (channels and groups)

Running and growing a Telegram channel or group: setup, positioning, post formatting, content calendars, polls, pinned posts, moderation, bots, cross-promotion, monetisation and analytics.

## Platform facts

| Item | Limit / note |
|---|---|
| Text message | 4,096 characters |
| Media caption | 1,024 characters (Premium accounts can send longer) |
| Channel description | 255 characters |
| Public @username | At least 5 characters: letters, digits, underscores |
| Subscribers | Channels have no subscriber limit. Groups go up to 200,000 members |
| Formatting | Bold, italic, underline, strikethrough, spoiler, inline code, code blocks, quotes (including expandable quotes), custom link text |
| Silent posts | Sent without a notification sound. Good for late-hour or minor posts |
| Scheduling | Built-in scheduled messages in the Telegram apps |
| Invite links | Multiple named links per channel, each with its own join count, optional join requests, expiry and member limits |
| Statistics | Channel statistics unlock once a channel passes a subscriber threshold |

## How visibility works

- There's no algorithmic feed. Subscribers see every post in order, so the main risk is being muted or left, not being hidden.
- Growth comes from forwards, mentions in other channels, links from other platforms and Telegram search for public usernames.
- Too many posts or too many notifications lead to mutes and unsubscribes. View rate (views per post ÷ subscribers) is the health signal.

## Intake (save to `social/telegram.md`)

- Channel, group or both. Public or private. Language(s).
- The one-sentence promise ("daily AI tools in 3 minutes") and audience.
- Current subscribers, views per post, posting frequency.
- Bots or tools already connected. Monetisation goals, if any.

## Deliverables

**Setup**: short searchable name and @username, a description that says what, how often and for whom, avatar, pinned welcome post (what to expect, links, rules), linked discussion group for comments, admin roles and post signatures.

**Content strategy**: one clear promise, a consistent rhythm (1–3 posts a day is typical), recurring series ("Monday Tools", "Friday Recap"), a mix of short insights, curated links with commentary, original images, short video and voice notes, polls and quizzes, and something exclusive that makes staying subscribed worth it. A weekly calendar.

**Post formatting**: bold headline, short paragraphs, emoji bullets in moderation, one CTA, link previews only when useful, silent posts for late hours. Write in the channel's language, with bilingual versions if the audience is mixed.

**Groups and moderation**: 5–7 rules, anti-spam (slow mode, join requests, admin or captcha bots), FAQ replies, an escalation path, reply templates.

**Growth**: cross-promotion swaps with similar-sized channels, links from other platforms and the website, one named invite link per source to track what works, content worth forwarding, giveaways that follow local law.

**Monetisation** (if wanted): sponsored posts (always labelled), paid subscriptions or premium groups, disclosed affiliate links, own products, and Telegram's own options (Stars, paid posts, ad revenue sharing). Eligibility rules for Telegram's options change, so check current terms.

**Analytics review**: subscriber growth and churn, view rate, forwards, reactions, top posts, which invite links bring members. Decide what to post more of and what to stop.

## Policy and ToS

- Telegram's Terms of Service ban spam and scams, and public channels can't host illegal content or promote violence.
- No buying subscribers or fake views. Label ads clearly.
- Never expose group members' personal data or phone numbers.

## Tools and posting

- The Bot API can post to a channel once the bot is added as an admin (`sendMessage`, `sendPhoto`, etc. with `parse_mode` HTML or MarkdownV2, and `disable_notification` for silent posts).
- Third-party schedulers and admin bots exist. Check what the user already uses.
- Draft everything; post or schedule only through the user's connected bot or tools, after they confirm.

## Review checklist

- [ ] Within the 4,096 / 1,024 character limit for the post type
- [ ] Formatting renders correctly in the chosen parse mode
- [ ] Silent flag set for late-hour or minor posts
- [ ] Ads and affiliate links labelled
- [ ] Posting frequency won't push people to mute
- [ ] User has approved before anything is posted
