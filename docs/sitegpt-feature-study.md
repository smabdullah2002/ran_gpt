# SiteGPT Feature Study (Competitive Baseline)

## Research Scope

This document summarizes publicly visible features from:
- https://sitegpt.ai/
- https://sitegpt.ai/features
- https://sitegpt.ai/integrations
- https://sitegpt.ai/pricing
- https://sitegpt.ai/security
- https://sitegpt.ai/lead-generation
- https://sitegpt.ai/docs/api-reference/getting-started
- https://sitegpt.ai/wordpress-plugin

## Product Positioning

SiteGPT positions itself as a website-trained AI support and lead-generation chatbot:
- trained on business content (URLs, files, raw text, integrated data sources)
- deployable on websites and support channels
- focused on 24/7 support automation + human escalation + lead capture

## Core Capability Groups

### 1) Knowledge Ingestion and Training

Observed capabilities:
- URL/sitemap based crawling and content sync
- File upload and raw text ingestion
- Multiple connected sources (Google Drive, Dropbox, OneDrive, SharePoint, Zendesk, GitBook, Box, Notion, Freshdesk, Confluence, Intercom, YouTube)
- Manual refresh in all plans
- Scheduled auto-refresh by plan (monthly/weekly/daily)
- Auto URL discovery/scan by plan (daily in higher plans)

Inferred product requirements:
- source connectors framework
- crawl pipeline (fetch, clean, chunk, dedupe)
- embeddings + retrieval index
- source/version tracking and resync jobs

### 2) Chat Experience and Deployment

Observed capabilities:
- embeddable chatbot on multiple properties (marketing site, app, help center)
- custom appearance (logo, colors, branding)
- quick prompts/conversation starters
- multi-language support (95+ languages claim)
- chat history and transcript review
- WordPress plugin (easy install + chatbot ID connection)

Inferred requirements:
- configurable embeddable widget
- conversation/session model
- localization-aware prompts + responses
- per-chatbot UI theme configuration

### 3) Operations, Support, and Human Handoff

Observed capabilities:
- escalate to human support
- integrations with support/chat tools (Crisp, Zendesk, Slack, Messenger, Freshchat, Zoho SalesIQ, Google Chat; some channels marked coming soon)
- webhook events for new messages, escalations, leads
- daily email summaries

Inferred requirements:
- escalation workflow state machine
- routing rules and destination adapters
- event bus + webhook delivery + retries
- summaries and reporting jobs

### 4) Lead Generation and Conversion

Observed capabilities:
- lead capture from chat
- lead qualification questions
- appointment booking integrations (Google Calendar, Outlook, Calendly, Cal.com)
- CRM automation messaging (HubSpot, Zapier references)

Inferred requirements:
- lead model with qualification fields
- conversation-to-lead extraction rules
- scheduling and CRM connectors
- conversion analytics dashboards

### 5) Platform and Developer Features

Observed capabilities:
- API access with bearer key authentication
- REST resources for chatbots, appearance, content, messages, threads, settings, prompts, quick prompts, follow-up prompts, icons, white-label
- response envelope format, pagination, rate limiting
- webhook support

Inferred requirements:
- stable public API and API key lifecycle
- tenant-aware authorization and quotas
- internal event-driven architecture

### 6) Security, Compliance, and Governance

Observed capabilities:
- SOC 2 Type II, GDPR, HIPAA positioning
- encrypted in transit + at rest
- role-based access control
- DPA/BAA availability on enterprise
- explicit claim: customer data not used to train models

Inferred requirements:
- tenant isolation and strict RBAC
- auditable access logs and data retention controls
- encryption + secrets management
- privacy controls, export/delete workflows

### 7) Pricing and Packaging Strategy

Observed plan dimensions:
- number of chatbots
- monthly messages
- training pages (defined by cleaned characters)
- team members
- integration/API availability by tier
- automation levels (manual refresh vs scheduled refresh, auto-scan)
- add-ons and white-label options

Inferred requirements:
- usage metering by message + content units
- feature flags per plan
- limits enforcement and overage billing workflow

## Baseline Feature Matrix for Our Build

### Phase 1 MVP (must-have)

- Auth and multi-tenant teams
- One chatbot per tenant initially
- URL + file + raw text ingestion
- RAG chat with citations
- Embeddable website widget 
- Chat history dashboard
- Lead capture form + basic export
- Human escalation to email and Slack
- Usage metering (messages, pages/chars)
- API keys and minimal public API

### Phase 2 Growth (high impact)

- Scheduled refresh + sitemap auto-scan
- More channel integrations (Zendesk, Crisp, Messenger)
- Analytics dashboard (deflection, CSAT proxy, lead conversion)
- Q and A feedback loop training
- Advanced prompt and persona controls
- White-label and custom branding domains

### Phase 3 Enterprise

- Fine-grained RBAC and SSO/SAML
- Compliance tooling (DPA workflow, audit exports)
- Data residency options
- custom connector framework
- SLA, priority support tooling

## Suggested Product Differentiators (to avoid pure clone)

- Better source quality scoring (detect stale or low-confidence pages automatically)
- Built-in answer confidence threshold + safe fallback flows
- Strong citation UX for trust
- Integrated experiment mode for prompt/version A/B tests
- Agentic workflows for support actions with explicit approvals

## Key Risks to Address Early

- Hallucinations from low-quality content ingestion
- poor source freshness if sync jobs fail silently
- integration reliability and webhook delivery failures
- cost spikes from unmetered LLM usage
- privacy/compliance gaps in conversation storage

## Success Metrics

- first-response time from chat open
- answer acceptance rate (thumbs up or solved)
- ticket deflection rate
- escalation rate and escalation quality
- lead capture rate and qualified lead rate
- cost per resolved conversation
- monthly active chatbots and retention
