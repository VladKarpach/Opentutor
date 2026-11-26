<div align="center">

<img src="https://img.icons8.com/3d-fluency/94/graduation-cap.png" alt="OpenTutor Logo" width="80" />

# OpenTutor

**Self-hosted, local-first AI learning platform.**

Upload your course material and get an AI tutor that adapts to how you learn — all running on your machine, completely free.

[Русский](./README.md) | **English**

</div>

## What it is

OpenTutor is a **self-hosted, local-first** AI learning platform built with FastAPI (backend) and Next.js (frontend). Upload PDFs, DOCX, PPTX, or connect Canvas LMS — the system ingests your material and gives you an adaptive AI tutor, quizzes, flashcards, study plans, and a block-based workspace. Nothing leaves your machine unless you explicitly connect a cloud LLM provider.

## Quick Start

### Option 1: Docker (recommended)

```bash
cd OpenTutor
cp .env.example .env
docker compose up -d --build
```

Open [http://localhost:3001](http://localhost:3001). Done.

API is available at `http://localhost:8000/api` (ports can be changed via `API_PORT` and `WEB_PORT` in `.env`).

### Option 2: Without Docker (manual)

```bash
# From OpenTutor/ root

# 1. Environment
cp .env.example .env

# 2. Backend
cd apps/api
python3 -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1
source .venv/bin/activate
pip install -r requirements-core.txt
uvicorn main:app --reload --port 8000

# 3. Frontend (separate terminal)
cd ../web
npm install
npm run dev
```

Open [http://localhost:3001](http://localhost:3001).

## Configuration

Copy `.env.example` to `.env` and adjust as needed. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | LLM provider |
| `LLM_MODEL` | `llama3.2:3b` | Model name |
| `DATABASE_URL` | `sqlite+aiosqlite:///~/.opentutor/data.db` | SQLite-only database path |
| `API_PORT` | `8000` | Backend port |
| `WEB_PORT` | `3001` | Frontend port |
| `AUTH_ENABLED` | `false` | Authentication (keep `false` for local single-user) |
| `APP_AUTO_CREATE_TABLES` | `true` | Auto-create DB tables on startup |

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| **Frontend** | Next.js 16, React 19, TypeScript, Tailwind CSS 4, Zustand, shadcn/ui |
| **Backend** | FastAPI, Python 3.11+, Pydantic 2, SQLAlchemy 2 (async), Alembic |
| **Database** | SQLite (local-first) |
| **LLM Router** | Multi-provider (Ollama, OpenAI, Anthropic, DeepSeek, Gemini, Groq, vLLM, LM Studio, OpenRouter, custom) |
| **Learning Science** | FSRS 4.5, BKT, LOOM, LECTOR, Cognitive Load Theory |
| **Ingestion** | PDF, DOCX, PPTX, XLSX, CSV, HTML, Canvas LMS, web scraping |

## Project Layout (from `OpenTutor/`)

```
OpenTutor/
├── apps/
│   ├── api/              # FastAPI backend
│   │   ├── services/     # agent, ingestion, llm, search, spaced_repetition, ...
│   │   ├── routers/      # API route modules
│   │   └── models/       # SQLAlchemy ORM models
│   └── web/              # Next.js 16 frontend
│       └── src/
├── tests/                # pytest unit/integration + Playwright E2E
├── docs/                 # PRD, architecture decisions, troubleshooting
├── scripts/              # dev, CI, and deployment scripts (bash)
├── docker-compose.yml
├── .env.example
├── pytest.ini
├── playwright.config.ts
└── package.json          # root: @playwright/test only
```

</div>
