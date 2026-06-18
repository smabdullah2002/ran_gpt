# SaaS Workplan and System Design

Project goal: build a SiteGPT-like multi-tenant AI support platform using React (JSX) for frontend, FastAPI for backend, Supabase for database/auth/storage/realtime, Pinecone for vector storage, HuggingFace Inference API for embeddings, and Gemini for LLM.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Frontend | React (JSX) + Vite |
| Backend | FastAPI |
| Database / Auth / Storage / Realtime | Supabase (Postgres) |
| Vector Store | Pinecone (384d, cosine, per-chatbot namespace) |
| Embeddings | HuggingFace Inference API (all-MiniLM-L6-v2) |
| LLM | Gemini |
| Crawler | Crawlee + Playwright |
| HTML-to-text | Trafilatura |
| Chunking | LangChain text splitters |
| Job Queue | Redis + worker process |

---

## Architecture

```
URL Input
│
▼
crawlee + playwright ← crawl all pages
│
▼
trafilatura ← clean text from HTML
│
▼
langchain-text-splitters ← split into chunks
│
▼
HuggingFace Inference API ← embed chunks
(all-MiniLM-L6-v2) (384 dimensions)
│
▼
Pinecone ← store & search vectors
│
▼
FastAPI
├── POST /ingest ← trigger crawl + embed
└── POST /chat ← query → retrieve → Gemini → answer
```

### Logical Architecture

- **Frontend (React + Vite)**
  - Marketing site
  - Authenticated dashboard
  - Embedded widget app
- **Backend API (FastAPI)**
  - tenant-aware REST APIs
  - ingestion orchestration
  - chat orchestration (RAG + LLM)
  - integration/webhook services
- **Data Platform (Supabase)**
  - Postgres (core relational data)
  - Auth (users, sessions)
  - Storage (uploaded files)
  - Realtime (live conversation updates)
- **Vector Store (Pinecone)**
  - Embeddings storage + semantic search
- **Workers/Jobs**
  - crawl + parsing + chunking
  - embedding generation
  - scheduled refresh and auto-scan
  - webhook delivery retries
  - digest/email summary jobs
- **External Providers**
  - Gemini (LLM)
  - HuggingFace Inference API (embeddings)
  - email provider
  - optional connectors (Slack/Zendesk/Calendly/etc)

---

## Request/Response Flow (Chat)

1. Visitor sends message from widget.
2. FastAPI resolves chatbot, tenant, and policy.
3. Retrieve relevant chunks from Pinecone using similarity search.
4. Compose prompt with instructions + retrieved context + guardrails.
5. Call Gemini and produce answer + citations.
6. Persist message, token usage, and metadata.
7. Emit events (lead detected, escalation required, analytics counters).
8. Return response to widget.

---

## Core Services (FastAPI)

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
- semantic retrieval over per-chatbot Pinecone index
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

---

## Data Model

### Supabase Tables

| Table | Purpose |
|---|---|
| `organizations`, `users`, `organization_members`, `api_keys` | Tenant & auth |
| `chatbots`, `chatbot_settings`, `chatbot_appearance`, `prompts`, `quick_prompts` | Chatbot config |
| `sources`, `source_sync_runs`, `source_documents`, `document_chunks` | Ingestion tracking |
| `threads`, `messages`, `message_citations`, `leads`, `escalations` | Runtime data |
| `usage_events`, `plan_subscriptions`, `webhook_endpoints`, `webhook_deliveries`, `audit_logs` | Ops & billing |

### Pinecone Resource

| Resource | Purpose |
|---|---|
| Index `chatbot-embeddings` (384d, cosine) | Vector storage per chatbot namespace |

### Supabase RLS Strategy
- all business tables include organization_id
- row-level policies enforce membership by JWT subject
- service role only for backend worker jobs
- separate policies for widget anonymous traffic using chatbot public token

---

## API Surface (v1)

### Dashboard APIs
- `POST /api/v1/chatbots`
- `GET /api/v1/chatbots`
- `GET /api/v1/chatbots/{id}`
- `PATCH /api/v1/chatbots/{id}`
- `DELETE /api/v1/chatbots/{id}`
- `POST /api/v1/chatbots/{id}/sources`
- `DELETE /api/v1/chatbots/{id}/sources/{source_id}`
- `POST /api/v1/chatbots/{id}/sync`
- `GET /api/v1/chatbots/{id}/conversations`
- `GET /api/v1/chatbots/{id}/analytics`
- `POST /api/v1/chatbots/{id}/webhooks`

### Widget APIs (public, unauthenticated)
- `POST /api/v1/widget/session`
- `POST /api/v1/widget/message`
- `GET /api/v1/widget/quick-prompts`
- `POST /api/v1/widget/lead`
- `POST /api/v1/widget/escalate`

### Platform APIs
- `POST /api/v1/api-keys`
- `GET /api/v1/api-keys`
- `DELETE /api/v1/api-keys/{id}`
- `GET /api/v1/usage`
- `POST /api/v1/webhooks/test`

---

## Phased Workplan

### Phase 0 — Foundation (Week 1)

**Backend**
- FastAPI app skeleton with health endpoint
- Supabase project setup — schemas, RLS policies, initial migration
- Supabase Auth integration (JWT verification middleware)
- Tenant isolation middleware
- Config management (env vars for Pinecone, HF, Gemini, Supabase)

**Frontend**
- React Router setup (marketing site, dashboard, auth pages)
- Supabase client + auth context
- Login/register pages
- Dashboard shell with sidebar navigation

**Infra**
- monorepo conventions, lint, CI config
- Docker compose for local dev (FastAPI + worker + Redis)

**Exit criteria:**
- local dev starts frontend and backend
- auth works for dashboard
- CI runs lint and tests

---

### Phase 1 — MVP Core (Weeks 2–5)

#### 1.1 Chatbot CRUD
- **Backend**: `POST/GET/PATCH/DELETE /api/v1/chatbots` — CRUD with settings, appearance, prompt config
- **Frontend**: Chatbot list view, create wizard, settings page (prompt, persona, quick prompts, theme)

#### 1.2 Ingestion Pipeline
- **Crawler**: Crawlee + Playwright crawler worker
- **Cleaner**: Trafilatura HTML-to-text extraction
- **Chunker**: LangChain text splitters (recursive character)
- **Embedder**: HuggingFace Inference API → 384d vectors
- **Vector store**: Pinecone index (per-chatbot namespace)
- **Sources**: URL + file upload + raw text
- **Job queue**: Background worker for async crawl → clean → chunk → embed → store

#### 1.3 Widget Chat (RAG)
- **Backend**: `POST /api/v1/widget/session`, `POST /api/v1/widget/message`
  - Retrieve from Pinecone → compose prompt → call Gemini → return answer + citations
- **Frontend (widget)**: Lightweight embeddable React widget with theming, quick prompts, session persistence
- **Frontend (dashboard)**: Chat history inbox

#### 1.4 Lead Capture + Escalation
- **Backend**: Lead model, extraction rules, `POST /api/v1/widget/lead`
- **Escalation**: Slack webhook + email (SMTP/SendGrid)
- **Frontend**: Lead capture card in widget, leads table in dashboard

**Exit criteria:**
- customer can go from URL to live embedded chatbot
- conversation data visible in dashboard
- leads and escalations delivered

---

### Phase 2 — Reliability & Product Depth (Weeks 6–8)

- Scheduled sync + source health status UI
- Usage metering (messages, pages) + plan enforcement
- Analytics dashboard (response quality proxy, deflection)
- API key management + webhook events
- Feedback loop (thumbs up/down on answers)

**Exit criteria:**
- limits and metering are enforced
- admins can monitor quality and source freshness

---

### Phase 3 — Integrations & Growth (Weeks 9–11)

- Zendesk, Crisp adapters
- Calendly/Google Calendar booking integration
- Lead qualification workflows
- Export/reporting

**Exit criteria:**
- at least two support channel integrations stable in production
- leads can flow to external systems

---

### Phase 4 — Security & Launch (Week 12)

- Audit logs, retention controls, GDPR export/delete
- Penetration testing, rate limiting hardening
- Runbooks, alerts, monitoring
- Beta onboarding checklist

**Exit criteria:**
- security baseline completed
- production SLOs and alerts active

---

## Scaling Strategy

### Handling 100 to 10,000 Users
- Stage 1 (100–500): single API instance + managed Postgres, basic caching
- Stage 2 (500–2,000): horizontal API scaling, dedicated worker process, Redis cache
- Stage 3 (2,000–10,000): autoscaling API/workers, read replicas, partitioned event tables

### Async Processing
- move non-interactive workloads to queues: crawling, parsing, chunking, embeddings, webhook delivery, summaries
- at-least-once job execution with idempotency keys
- dead-letter queues and exponential backoff retries
- queue observability: backlog size, retry count, failure rate, oldest job age

### Rate Limiting
- per-IP limits on public widget APIs
- per-organization and per-chatbot quotas per plan
- token bucket or sliding-window limits at API gateway and service layers
- clear 429 responses with retry metadata

### Cost Optimization (LLM Usage)
- route to smaller models by default; escalate on low confidence
- cap context size with top-k retrieval and token budget guards
- cache frequent Q&A pairs and semantic-near-duplicate prompts
- response streaming cutoffs and max-turn policies
- track unit economics: cost per resolved conversation, cost per lead

---

## Frontend Design

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

---

## Engineering Standards

- strict tenant isolation in every query
- all LLM answers grounded with retrievable citations
- idempotent job design and dead-letter handling
- webhook signing + exponential backoff retries
- observability: structured logs, traces, metric dashboards

---

## Deployment and Infra

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

---

## Definition of Done for MVP

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
