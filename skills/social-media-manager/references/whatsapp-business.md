# WhatsApp Business

WhatsApp for customer service and sales: business profile, catalog, quick replies, greeting and away messages, labels, support scripts, opt-in broadcasts, WhatsApp Channels, Business API message templates and chatbot flows.

## Two different products

| | WhatsApp Business app | WhatsApp Business Platform (Cloud API) |
|---|---|---|
| Who | Small businesses, one phone number, a few devices | Businesses that need automation, many agents or integrations |
| Set up | Free app on a phone | Through Meta directly or a Business Solution Provider (Twilio, 360dialog, etc.). Meta's on-premises API has been retired |
| Tools | Catalog, greeting and away messages, quick replies, labels, broadcast lists | Message templates, webhooks, chatbots, CRM integration, Flows |
| Cost | App is free; some markets now charge for marketing broadcasts | Meta charges per template message by category and country. Pricing changed in 2025 and changes again, so check current rates |

## Platform facts

- **24-hour customer service window**: after a customer messages, the business can reply freely for 24 hours. Outside that window, only approved templates can be sent.
- **Template categories**: Marketing, Utility, Authentication. Meta reviews each template and can recategorise it. Variables look like `{{1}}`. Buttons can be quick replies, URLs or phone calls.
- **Messaging limits**: new API numbers can only start conversations with a limited number of people per day. The limit rises as the number keeps a good quality rating, and falls if people block or report it.
- **Broadcast lists (Business app)**: only people who saved your number receive them, and each list has a size cap.
- **Channels**: one-way update feeds. Followers don't see each other and admins don't see followers' phone numbers.
- **AI chatbots**: Meta's Business Solution terms (effective January 2026) bar general-purpose AI assistants as the main product on the API. Bots that serve the business's own customers (orders, support, bookings) are fine. Check the current terms if the user is building an AI bot.

## Intake (save to `social/whatsapp.md`)

- Business app or API? Which provider, if API?
- What customers ask most (prices, hours, delivery, order status, booking).
- Products for the catalog, payment and order process.
- How contacts opted in, and the record of that consent.
- Who answers and when. Hand-off rules to a human.

## Deliverables

**Profile**: name, category, description, hours, address, website, catalog (photos, prices, descriptions), a `wa.me` short link and QR code for the website and packaging.

**Business app automation**
- Greeting message: welcome, what you can help with, expected reply time.
- Away message: hours and when you'll reply.
- Quick replies (`/price`, `/delivery`, `/hours`, `/order`) written for this business.
- Labels: New lead, Pending payment, Order complete, VIP, Support.

**Conversation scripts**: sales (qualify → recommend → handle objections → close with a payment or order link), support (acknowledge → get the order number → resolve → confirm), post-purchase (thank, then ask for a review). Short, human messages, with images or voice notes where they help.

**Broadcasts and Channels**: opted-in contacts only, rarely (weekly at most is a sensible ceiling), valuable and personal, with an easy opt-out. A Channel plan for one-way offers, news and tips.

**API templates**: drafted in the right category with variables, sample values and buttons, written to pass Meta's review (clear purpose, no misleading urgency, business name present).

**Chatbot flow**: a menu of choices, one path per intent, fallback, and a hand-off to a human. Output a Mermaid diagram plus the message copy.

**Metrics review**: first-response time, conversations → orders, broadcast reply and opt-out/block rates, template quality ratings, frequent questions to add to quick replies or the FAQ.

## Policy and ToS

- WhatsApp Business Messaging Policy: business-initiated messages need explicit opt-in that names the business. Never buy or scrape contact lists. Blocks and reports lower the quality rating and can get the number restricted or banned.
- The WhatsApp Commerce Policy bans selling some goods and services. Check it for regulated products.
- Don't ask for sensitive data (card numbers, passwords, health details) in chat. Protect customer data under local privacy law.

## Tools and posting

- Business app: the user sends from their phone or WhatsApp Web.
- API: through the user's provider or Meta's Cloud API, with templates approved in WhatsApp Manager.
- Draft everything; send messages, broadcasts or templates only through the user's connected tools, after they confirm.

## Review checklist

- [ ] Every recipient has opted in
- [ ] Message is inside the 24-hour window, or uses an approved template
- [ ] Template is in the right category with sample values
- [ ] Bot has a clear hand-off to a human
- [ ] No sensitive data requested in chat
- [ ] User has approved before anything is sent
