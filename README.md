<div align="center">

# OpenTutor

**Самостоятельно размещаемая (self-hosted), локальная платформа для обучения с ИИ.**

Загрузите учебные материалы и получите ИИ-репетитора, который адаптируется под ваш стиль обучения — всё работает на вашем компьютере, совершенно бесплатно.

**English** | (./README.en-CN.md)

</div>

## Что это

OpenTutor — это **самостоятельно размещаемая, локальная** платформа для обучения на базе FastAPI (бэкенд) и Next.js (фронтенд). Загружайте PDF, DOCX, PPTX или подключайте Canvas LMS — система проанализирует ваши материалы и предоставит адаптивного ИИ-репетитора, тесты, карточки, планы обучения и блоковый рабочий стол. Данные не покидают ваш компьютер, пока вы не подключите облачного провайдера LLM по своему усмотрению.

## Быстрый старт

### Вариант 1: Docker (рекомендуется)

```bash
cd OpenTutor
cp .env.example .env
docker compose up -d --build
```

Откройте [http://localhost:3001](http://localhost:3001). Готово.

API доступно по адресу `http://localhost:8000/api` (порты можно изменить через `API_PORT` и `WEB_PORT` в `.env`).

### Вариант 2: Без Docker (ручной запуск)

```bash
# Из корня OpenTutor/

# 1. Окружение
cp .env.example .env

# 2. Бэкенд
cd apps/api
python3 -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1
source .venv/bin/activate
pip install -r requirements-core.txt
uvicorn main:app --reload --port 8000

# 3. Фронтенд (в отдельном терминале)
cd ../web
npm install
npm run dev
```

Откройте [http://localhost:3001](http://localhost:3001).


## Конфигурация

Скопируйте `.env.example` в `.env` и настройте по необходимости. Основные переменные:

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `LLM_PROVIDER` | `ollama` | Провайдер LLM |
| `LLM_MODEL` | `llama3.2:3b` | Название модели |
| `DATABASE_URL` | `sqlite+aiosqlite:///~/.opentutor/data.db` | Путь к базе SQLite (только SQLite) |
| `API_PORT` | `8000` | Порт бэкенда |
| `WEB_PORT` | `3001` | Порт фронтенда |
| `AUTH_ENABLED` | `false` | Аутентификация (для локального режима одного пользователя оставьте `false`) |
| `APP_AUTO_CREATE_TABLES` | `true` | Автоматически создавать таблицы БД при запуске |

## Стек технологий

| Слой | Технологии |
|------|------------|
| **Фронтенд** | Next.js 16, React 19, TypeScript, Tailwind CSS 4, Zustand, shadcn/ui |
| **Бэкенд** | FastAPI, Python 3.11+, Pydantic 2, SQLAlchemy 2 (async), Alembic |
| **База данных** | SQLite (локальный режим) |
| **Роутер LLM** | Несколько провайдеров (Ollama, OpenAI, Anthropic, DeepSeek, Gemini, Groq, vLLM, LM Studio, OpenRouter, кастомный) |
| **Наука об обучении** | FSRS 4.5, BKT, LOOM, LECTOR, теория когнитивной нагрузки |
| **Загрузка контента** | PDF, DOCX, PPTX, XLSX, CSV, HTML, Canvas LMS, веб-скрапинг |

## Структура проекта (из `OpenTutor/`)

```
OpenTutor/
├── apps/
│   ├── api/              # Бэкенд на FastAPI
│   │   ├── services/     # agent, ingestion, llm, search, spaced_repetition, ...
│   │   ├── routers/      # Модули маршрутов API
│   │   └── models/       # Модели ORM SQLAlchemy
│   └── web/              # Фронтенд на Next.js 16
│       └── src/
├── tests/                # Тесты pytest + Playwright E2E
├── docs/                 # PRD, архитектурные решения, руководство по устранению неполадок
├── scripts/              # Скрипты dev, CI и развёртывания (bash)
├── docker-compose.yml
├── .env.example
├── pytest.ini
├── playwright.config.ts
└── package.json          # Корневой: только @playwright/test
```


</div>
