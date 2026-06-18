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
