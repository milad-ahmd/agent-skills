---
name: cost-cutter
description: Finds ways to cut cloud and SaaS spend (AWS, GCP, Azure, Vercel, Netlify, Supabase, Firebase, OpenAI and other LLM APIs, databases, CDNs) by analyzing billing exports and infrastructure code, then ranks savings by dollars and effort with exact fixes and risks. Use when the user asks to reduce cloud costs, review a cloud bill, optimize infrastructure spend, cut LLM API costs, or asks why their bill is so high.
---

# Cost Cutter 💸

Every recommendation comes with a dollar estimate, the effort and the risk.

## 1. Gather (read-only)

- **The bill**: a cost export by service and usage type (AWS Cost Explorer CSV, GCP billing export, Vercel or Supabase usage pages, LLM usage dashboards). Ask for the last 1–3 months.
- **Infrastructure as code** in the repo: Terraform, CDK, Pulumi, Kubernetes manifests, `serverless.yml`, `vercel.json`, Dockerfiles, CI workflows.
- **The code paths that drive cost**: LLM calls (model, max tokens, retries, caching), queries, cron jobs, image and video processing, logging volume.
- Use read-only CLI commands only if the user approves (for example `aws ce get-cost-and-usage`).

## 2. Checklist (common big wins)

| Area | Look for |
|---|---|
| Compute | Idle/oversized instances, dev envs running 24/7, no autoscaling, x86 where ARM (Graviton) works, on-demand where Savings Plans/committed use fit steady load |
| Storage | No lifecycle rules, old snapshots/backups, unattached volumes, logs kept forever, S3 Standard for cold data |
| Network | NAT gateway data processing, cross-AZ/region traffic, missing CDN caching, egress-heavy assets |
| Databases | Oversized instances, unused read replicas, missing indexes causing big scans, provisioned IOPS not needed |
| Serverless / edge | Long durations, over-provisioned memory, chatty functions, missing caching/ISR, bandwidth overages |
| LLM APIs | Largest model for every task, no prompt caching, huge contexts, no `max_tokens`, retries without backoff, no batch API for offline jobs, missing response cache |
| Observability | Log/metric ingestion volume, debug logging in prod, high-cardinality metrics |
| SaaS | Unused seats, overlapping tools, monthly vs annual plans |

## 3. Report

```markdown
## 💸 Savings report: est. $X/month (Y% of spend)
| # | Change | Est. saving/mo | Effort | Risk | How |
|---|---|---|---|---|---|
| 1 | Route classification calls to a smaller model | ~$420 | S | Low | change model in `src/ai/classify.ts` + eval 50 samples |
```

Order by saving ÷ effort. Show the estimation maths (usage × unit price) and **label every number as an estimate**. Prices change, so tell the user to confirm on the provider's pricing page.

## 4. Implement (only after approval)

One change at a time, through infrastructure-as-code or a PR, with a rollback note and a metric to watch (latency, error rate, quality evaluations for model changes).

## Rules

- Never delete resources, data or backups without explicit confirmation and a backup plan.
- Don't trade away reliability or security without stating the trade-off.
