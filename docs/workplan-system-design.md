# SaaS Workplan and System Design

Project goal: build a SiteGPT-like multi-tenant AI support platform using React (JSX) for frontend, FastAPI for backend, and Supabase for database/auth/storage/realtime.

## 1) Product Scope

### Primary Use Cases

- Train a chatbot on business content (website pages, uploaded files, connected sources)
- Embed chatbot on customer websites and web apps
- Answer questions with retrieval grounded responses
- Capture leads and escalate to humans when needed
- Provide team dashboards for chat history, source health, and performance

### Non-Goals for Initial MVP

- Full parity with every channel integration
- enterprise SSO/SAML and advanced compliance workflows
- complex workflow automation marketplace

## 2) Architecture Overview

## Logical Architecture

- Frontend (React + Vite)
  - Marketing site
  - Authenticated dashboard
  - Embedded widget app
- Backend API (FastAPI)
  - tenant-aware REST APIs
  - ingestion orchestration
  - chat orchestration (RAG + LLM)
  - integration/webhook services
- Data Platform (Supabase)
  - Postgres (core relational data)
  - pgvector (embeddings)
  - Auth (users, sessions)
  - Storage (uploaded files)
  - Realtime (live conversation updates)
- Workers/Jobs
  - crawl + parsing + chunking
  - embedding generation
  - scheduled refresh and auto-scan
  - webhook delivery retries
  - digest/email summary jobs
- External Providers
  - LLM + embeddings provider
  - email provider
  - optional connectors (Slack/Zendesk/Calendly/etc)

## Request/Response Flow (Chat)

1. Visitor sends message from widget.
2. FastAPI resolves chatbot, tenant, and policy.
3. Retrieve relevant chunks from pgvector using similarity search.
4. Compose prompt with instructions + retrieved context + guardrails.
5. Call LLM and produce answer + citations.
6. Persist message, token usage, and metadata.
7. Emit events (lead detected, escalation required, analytics counters).
8. Return response to widget.

## 3) Core Services (FastAPI)

### Auth and Tenant Service

- Supabase JWT verification
- user membership and role enforcement
- tenant scoping middleware

### Chatbot Management Service

- chatbot CRUD
- appearance and behavior settings
- prompt/persona and quick prompts configuration

### Ingestion Service

- source registration (url/file/raw text/integration)
- crawler kickoff and source snapshoting
- content cleaning, chunking, dedupe, embedding pipelines

### Retrieval and Chat Service

- semantic retrieval over per-chatbot index
- grounded response generation with citations
- language handling and fallback strategies

### Conversations and Leads Service

- thread/message lifecycle
- lead extraction and qualification fields
- escalation state transitions

### Integrations Service

- outbound webhooks with signing and retries
- connector adapters (Slack first, then Zendesk/Crisp)

### Billing and Metering Service

- count messages, page-equivalent units, storage
- enforce plan limits and rate limits
- prepare invoicing/overage events

## 4) Data Model (Supabase/Postgres)

### Tenant and Access

- organizations
- users (via Supabase auth.users + profile)
- organization_members (role: owner, admin, editor, analyst)
- api_keys

### Chatbot Configuration

- chatbots
- chatbot_settings
- chatbot_appearance
- prompts
- quick_prompts

### Content and Indexing

- sources
- source_sync_runs
- source_documents
- document_chunks
- embeddings (vector)

### Runtime Conversations

- threads
- messages
- message_citations
- leads
- escalations

### Operations and Billing

- usage_events
- plan_subscriptions
- webhook_endpoints
- webhook_deliveries
- audit_logs

## Supabase RLS Strategy

- all business tables include organization_id
- row-level policies enforce membership by JWT subject
- service role only for backend worker jobs
- separate policies for widget anonymous traffic using chatbot public token

## 5) API Surface (v1)

### Dashboard APIs

- POST /api/v1/chatbots
- GET /api/v1/chatbots
- GET /api/v1/chatbots/{id}
- PATCH /api/v1/chatbots/{id}
- POST /api/v1/chatbots/{id}/sources
- POST /api/v1/chatbots/{id}/sync
- GET /api/v1/chatbots/{id}/conversations
- GET /api/v1/chatbots/{id}/analytics
- POST /api/v1/chatbots/{id}/webhooks

### Widget APIs

- POST /api/v1/widget/session
- POST /api/v1/widget/message
- GET /api/v1/widget/quick-prompts
- POST /api/v1/widget/lead
- POST /api/v1/widget/escalate

### Platform APIs

- POST /api/v1/api-keys
- GET /api/v1/usage
- POST /api/v1/webhooks/test

## 6) Scaling Strategy

### Handling 100 to 10,000 Users

- Stage 1 (100 to 500 users): single API instance + managed Postgres, basic caching
- Stage 2 (500 to 2,000 users): horizontal API scaling, dedicated worker process, Redis cache
- Stage 3 (2,000 to 10,000 users): autoscaling API/workers, read replicas, partitioned event tables
- Track saturation early using p95 latency, queue lag, DB CPU, and token spend per conversation

### Async Processing (Queues)

- move non-interactive workloads to queues: crawling, parsing, chunking, embeddings, webhook delivery, summaries
- use at-least-once job execution with idempotency keys to avoid duplicate side effects
- configure dead-letter queues and retry policies with exponential backoff
- expose queue observability: backlog size, retry count, failure rate, oldest job age

### Rate Limiting

- enforce per-IP limits on public widget APIs to reduce abuse
- enforce per-organization and per-chatbot quotas to match paid plans
- apply token bucket or sliding-window limits at API gateway and service layers
- return clear 429 responses with retry metadata for client-side backoff

### Cost Optimization (LLM Usage)

- route requests to smaller models by default; escalate to larger models only on low confidence
- cap context size with top-k retrieval and token budget guards
- cache frequent Q and A pairs and semantic-near-duplicate prompts
- add response streaming cutoffs and max-turn policies for runaway conversations
- track unit economics: cost per resolved conversation, cost per lead, and model-specific spend

## 7) Frontend Design

### Apps

- Dashboard app (authenticated)
- Embeddable widget app (public lightweight bundle)

### Dashboard Modules

- onboarding wizard (create chatbot, add source, test, embed)
- chatbot settings (prompt, behavior, appearance)
- sources and sync status
- conversation inbox and escalation queue
- leads view and export
- usage and billing

### Widget Requirements

- async script loader
- theming from chatbot settings
- quick prompts and multi-language UI strings
- conversation persistence via session token
- optional lead capture card and escalation button

## 8) Workplan (Execution)

### Phase 0: Foundation (Week 1)

- set monorepo conventions and env handling
- create FastAPI app skeleton and health endpoints
- set Supabase project, schemas, and baseline RLS
- create React dashboard shell and routing

Exit criteria:
- local dev starts frontend and backend
- auth works for dashboard
- CI runs lint and tests

### Phase 1: MVP Core (Weeks 2-5)

- chatbot CRUD + settings UI
- ingestion from URL + file + raw text
- chunking and embeddings pipeline
- widget chat with RAG answers and citations
- conversation logging and history UI
- basic lead capture and Slack/email escalation

Exit criteria:
- customer can go from URL to live embedded chatbot
- conversation data visible in dashboard
- leads and escalations delivered

### Phase 2: Reliability and Product Depth (Weeks 6-8)

- scheduled sync and source health status
- usage metering and plan enforcement
- analytics dashboard (response quality proxy, deflection)
- API key management and external webhook events
- feedback loop for answer quality

Exit criteria:
- limits and metering are enforced
- admins can monitor quality and source freshness

### Phase 3: Integrations and Growth (Weeks 9-11)

- add Zendesk and Crisp adapters
- add booking integrations (Calendly first)
- improve lead qualification workflows
- add export/reporting improvements

Exit criteria:
- at least two support channel integrations stable in production
- leads can flow to external systems

### Phase 4: Security and Launch Readiness (Week 12)

- audit logs, retention controls, delete/export flows
- penetration and abuse testing
- runbooks, alerts, on-call dashboard
- launch checklist and beta onboarding

Exit criteria:
- security baseline completed
- production SLOs and alerts active

## 9) Engineering Standards

- strict tenant isolation in every query
- all LLM answers grounded with retrievable citations
- idempotent job design and dead-letter handling
- webhook signing + exponential backoff retries
- observability: structured logs, traces, metric dashboards

## 10) Deployment and Infra

### Environments

- dev, staging, production with separate Supabase projects

### Suggested Runtime

- FastAPI on container platform (Railway/Render/Fly/Cloud Run)
- background workers via separate process
- Redis for queue/caching if needed (Upstash/managed Redis)

### CI/CD

- frontend: lint + build
- backend: lint + unit/integration tests + migration checks
- gated deploy from main to staging then production

## 11) Initial Team Plan

- 1 product lead/founder
- 1 frontend engineer (dashboard + widget)
- 1 backend engineer (API + ingestion + workers)
- 1 full-stack engineer (integrations + analytics)
- 1 part-time QA/ops support

## 12) Risks and Mitigations

- LLM cost volatility
  - mitigation: model routing, caching, strict rate limits
- poor answer quality from noisy content
  - mitigation: source quality scoring and chunk validation
- integration drift and API changes
  - mitigation: adapter abstraction and contract tests
- compliance claims without implementation proof
  - mitigation: auditable controls before marketing claims

## 13) Definition of Done for MVP

- a paying customer can:
  - create account and chatbot
  - train with URL + files
  - embed widget on site
  - receive grounded answers with citations
  - view chat history
  - capture leads and receive escalation alerts
- system-level:
  - tenant-safe data access via RLS
  - usage metering recorded per message
  - error monitoring and alerting enabled
