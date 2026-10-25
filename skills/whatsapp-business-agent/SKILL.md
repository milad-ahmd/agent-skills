---
name: whatsapp-business-agent
description: Sets up and runs WhatsApp Business for customer service and sales, covering business profile, catalog, quick replies, greeting and away messages, labels, customer support scripts, opt-in broadcast campaigns, WhatsApp Channels content, Business API message templates, and chatbot conversation flows. Use when the user asks to use WhatsApp for business, write WhatsApp replies or broadcasts, design a WhatsApp chatbot flow, or create WhatsApp message templates.
---

# WhatsApp Business Agent

Turn WhatsApp into a fast, friendly sales and support channel without spamming anyone.

## 1. Profile setup

Business name, category, description, hours, address, website, catalog (products with photos, prices and descriptions), and a short link or QR code for the website and packaging.

## 2. Automation basics (WhatsApp Business app)

- A **greeting message** (welcome plus what you can help with plus expected reply time).
- An **away message** (hours and when you'll reply).
- **Quick replies** (`/price`, `/delivery`, `/hours`, `/order`) drafted for the business.
- **Labels**: New lead, Pending payment, Order complete, VIP, Support.

## 3. Conversation scripts

Sales (qualify → recommend → handle objections → close with a payment or order link), support (acknowledge → collect the order number → resolve → confirm), and post-purchase (thank, then follow up for a review). Keep messages short and human, and use voice notes or images where they help.

## 4. Broadcasts and Channels

- **Broadcasts only go to opted-in contacts**, who must have saved your number or given consent. Send them rarely (e.g. weekly at most), make them valuable and personal, and give an easy way to opt out.
- **WhatsApp Channels**: a one-way updates feed with offers, news and tips.

## 5. WhatsApp Business API / Cloud API

- Draft **message templates** in the right categories (Marketing, Utility, Authentication) with variables `{{1}}` and buttons, written to pass Meta's review.
- Chatbot **flow design**: a menu of choices, a path for each intent, and a hand-off to a human. Output it as a diagram (Mermaid) plus the message copy.
- Respect the **24-hour customer service window**. Outside it, only approved templates may be sent.

## 6. Metrics

Response time, conversations → orders, broadcast reply and opt-out rates, and frequent questions (to add to quick replies or the FAQ).

## Rules

- Never message people without consent, and never buy contact lists. This breaks WhatsApp's policies and can get the number banned.
- Protect customer data, and don't ask for sensitive information in chat.
- Send messages only through the user's connected tools, with their confirmation.
