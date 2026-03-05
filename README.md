# Book Editor

AI-powered multi-agent book revision pipeline. Upload an `.epub`, and a swarm of distributed AI agents will read, debate, revise, reorder, and reassemble your book into three variant editions — then an audience panel reviews each one.

**Only requires two environment variables:** `DATABASE_URL` and `OPENROUTER_API_KEY`

## Quick Start (Using Claude Code as Your Local Agent)

You're sitting at your Mac with Claude Code open. Your book editor is deployed on Railway. Here's how to use it end-to-end.

### Prerequisites

- [Railway CLI](https://docs.railway.com/guides/cli) installed and logged in (`brew install railway && railway login`)
- An [OpenRouter API key](https://openrouter.ai/keys) with credit loaded
- An `.epub` file of the book you want to edit

### 1. Set Your OpenRouter Key (one-time)

```bash
cd ~/slices/book-editor
railway vars --set 'OPENROUTER_API_KEY=sk-or-v1-your-actual-key-here'
```

This triggers an automatic redeploy. Wait ~60 seconds.

### 2. Upload Your Book

```bash
./author-tool-helper.sh upload /path/to/your-book.epub
```

Response:
```json
{
    "book_id": 1,
    "title": "Your Book Title",
    "author": "Author Name",
    "chapters": 14
}
```

**Remember your `book_id`** — you'll use it for everything.

### 3. Run the Micro-Book Dry Run First

This uses a **free model** to produce a ~200-word children's-book version. It proves the whole pipeline (parsing, DB, OpenRouter calls, agent coordination) works before you spend real money.

```bash
./author-tool-helper.sh micro 1
```

Check what it produced:
```bash
./author-tool-helper.sh drafts 1
./author-tool-helper.sh draft <draft_id>
```

### 4. Run the Full Pipeline

Once the micro dry run succeeds:

```bash
./author-tool-helper.sh full 1
```

This kicks off the full multi-agent editing pipeline (runs in the background on Railway). It will take a while — potentially 30-60+ minutes for a full book depending on chapter count.

Or run both micro + full in sequence:
```bash
./author-tool-helper.sh run-all 1
```

### 5. Monitor Progress

**Live watch** (refreshes every 10 seconds):
```bash
./author-tool-helper.sh watch 1
```

**One-time status check:**
```bash
./author-tool-helper.sh status 1
```

**View agent conversation log:**
```bash
./author-tool-helper.sh interactions 1
```

**Tail Railway server logs:**
```bash
./author-tool-helper.sh logs
```

### 6. Get Results

**List all assembled drafts:**
```bash
./author-tool-helper.sh drafts 1
```

The pipeline produces:
- **Draft version 0** — Micro-book (dry run)
- **Draft version 1** — Variant 1 (editor's primary chapter order)
- **Draft version 2** — Variant 2 (different opening chapter)
- **Draft version 3** — Variant 3 (different opening chapter)

**Get a specific draft with audience feedback:**
```bash
./author-tool-helper.sh draft <draft_id>
```

**View the judge's accumulated editorial memory:**
```bash
./author-tool-helper.sh memory 1
```

### 7. Ask Claude Code to Help You Review

Since you're working in Claude Code, you can ask your local agent to:

```
"Fetch the draft from https://book-editor-app-production.up.railway.app/drafts/3
and summarize the audience feedback"

"Compare the audience scores across all three variants"

"Pull the judge's memory and tell me what editorial patterns emerged"

"Show me which chapters were excluded by the editor and why"
```

Your local Claude can `curl` the API endpoints and analyze the results for you.

---

## How the Pipeline Works

```
UPLOAD .epub
     │
     ▼
┌─────────────┐    ┌──────────────┐
│ EPUB PARSER │───▶│  PostgreSQL   │  chapters as markdown
└─────────────┘    └──────┬───────┘
                          │
                   ┌──────▼───────┐
                   │  MICRO DRY   │  free model, ~200 words
                   │    RUN       │  proves pipeline works
                   └──────┬───────┘
                          │
              ┌───────────▼───────────┐
              │     EDITOR reads      │  gemini-2.5-pro (1M ctx)
              │     entire book       │  structural assessment
              └───────────┬───────────┘
                          │
              ┌───────────▼───────────┐
              │   STYLIST analyzes    │  voice analysis
              │   author's voice      │  style brief
              └───────────┬───────────┘
                          │
              ┌───────────▼───────────┐
              │  EDITOR ↔ STYLIST     │  3 rounds of debate
              │  debate on direction  │  architecture vs voice
              └───────────┬───────────┘
                          │
              ┌───────────▼───────────┐
              │   WORKER SWARM edits  │  concurrent chapter editing
              │   chapters (5x)       │  (up to 3 attempts each)
              │         │             │
              │   JUDGE validates     │  approve/reject + RAG memory
              └───────────┬───────────┘
                          │
              ┌───────────▼───────────┐
              │  EDITOR reorders      │  JSON chapter ordering
              │  + writes 3 variant   │  can exclude chapters
              │  first chapters       │
              └───────────┬───────────┘
                          │
              ┌───────────▼───────────┐
              │  3 AUDIENCE PERSONAS  │  Jordan, Maya, Robert
              │  review 3 drafts     │  3 rounds each = 27 reviews
              │  with scored feedback │
              └───────────────────────┘
```

## Agent Roles

| Agent | Model | What It Does |
|-------|-------|-------------|
| **Editor** | `google/gemini-2.5-pro` (1M ctx) | Reads the entire book in one shot. Makes structural decisions. Reorders/excludes chapters. Writes variant openings. Assembles final drafts. |
| **Stylist** | `google/gemini-2.5-flash` (1M ctx) | Loves the author's writing voice. Debates the editor. Creates a style brief that workers must follow. Flags when revisions lose the author's voice. |
| **Judge** | `anthropic/claude-sonnet-4` (200K) | Reviews every chapter revision. Approves or rejects with specific feedback. Maintains a learning memory (RAG-like) across all interactions. Workers can ask the judge questions. |
| **Workers** | `google/gemini-2.5-flash` (1M ctx) | Swarm of 5 concurrent chapter editors. Follow editor instructions + stylist's style brief. Submit to judge. Get up to 3 attempts per chapter. |
| **Audience** | `google/gemini-2.5-flash` (1M ctx) | 3 reader personas who review the complete book. Provide positive + critical feedback with scores. |
| **Micro** | `meta-llama/llama-3.3-70b:free` (128K) | Free model for dry runs. Produces a second-grade-level children's book summary. |

## Sacred Rules

These are hardcoded into the agent system prompts:

1. **Attributed quotations** from other authors or historical sources are **NEVER modified**
2. **The epilogue** written for the author's children **remains untouched**
3. The author's writing voice is **loved** — the problem is architecture, not prose
4. The author thinks in "an autistic painting of a logic tree" — the book may need **complete architectural rethinking** to be accessible

## Customizing Agent Prompts

All agent system prompts live in one file:

```bash
vim ~/slices/book-editor/agent_system_prompts.json
```

Edit any agent's personality, instructions, or constraints there. After editing:

```bash
cd ~/slices/book-editor
railway up --detach    # redeploy
```

## Changing Models

Edit `src/book_editor/config.py` to swap models:

```python
editor_model: str = "google/gemini-2.5-pro"       # 1M context, highest quality
stylist_model: str = "google/gemini-2.5-flash"     # 1M context, fast
judge_model: str = "anthropic/claude-sonnet-4"     # strong reasoning
worker_model: str = "google/gemini-2.5-flash"      # fast chapter editing
audience_model: str = "google/gemini-2.5-flash"    # roleplay feedback
micro_model: str = "meta-llama/llama-3.3-70b-instruct:free"  # free dry run
```

Or override via environment variables:
```bash
railway vars --set 'EDITOR_MODEL=google/gemini-2.5-pro'
railway vars --set 'WORKER_MODEL=google/gemini-2.0-flash-001'
```

## API Reference

All endpoints are available at your Railway URL. Swap in `http://localhost:8000` for local dev.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/books/upload` | Upload `.epub` (multipart form, field: `file`) |
| `GET` | `/books/{id}` | Book metadata + chapter list |
| `POST` | `/books/{id}/micro` | Start micro-book dry run |
| `POST` | `/books/{id}/full` | Start full editing pipeline |
| `POST` | `/books/{id}/run-all?skip_micro=false` | Run micro then full |
| `GET` | `/books/{id}/status` | Pipeline progress |
| `GET` | `/books/{id}/drafts` | List assembled drafts |
| `GET` | `/drafts/{id}` | Get draft text + audience feedback |
| `GET` | `/books/{id}/interactions?limit=50` | Agent conversation log |
| `GET` | `/books/{id}/judge-memory` | Judge's accumulated editorial memory |

## `author-tool-helper.sh` Commands

```
SETUP:
  ./author-tool-helper.sh setup           Initial Railway setup
  ./author-tool-helper.sh deploy          Deploy to Railway
  ./author-tool-helper.sh local-up        Start local Docker env
  ./author-tool-helper.sh local-down      Stop local Docker env

PIPELINE:
  ./author-tool-helper.sh upload <file>   Upload .epub
  ./author-tool-helper.sh micro <id>      Micro-book dry run
  ./author-tool-helper.sh full <id>       Full editing pipeline
  ./author-tool-helper.sh run-all <id>    Micro then full pipeline

MONITORING:
  ./author-tool-helper.sh status <id>     Pipeline status
  ./author-tool-helper.sh watch <id>      Live status (auto-refresh)
  ./author-tool-helper.sh interactions <id>  Agent interaction log
  ./author-tool-helper.sh memory <id>     Judge's editorial memory
  ./author-tool-helper.sh logs            Tail Railway logs

RESULTS:
  ./author-tool-helper.sh book <id>       Book metadata + chapters
  ./author-tool-helper.sh drafts <id>     List all drafts
  ./author-tool-helper.sh draft <id>      Get draft + audience feedback

ADMIN:
  ./author-tool-helper.sh db             PostgreSQL shell (Railway)
  ./author-tool-helper.sh ssh            Shell into Railway container
  ./author-tool-helper.sh env [KEY=VAL]  View/set Railway env vars
```

## Cost Estimates

Rough per-run costs via OpenRouter (varies by book length):

| Stage | Model | Estimated Cost |
|-------|-------|---------------|
| Micro dry run | llama-3.3-70b:free | **$0.00** |
| Editor reads full book | gemini-2.5-pro (1M input) | ~$1-5 |
| Stylist analysis + debate | gemini-2.5-flash | ~$0.50-2 |
| Worker swarm (per chapter) | gemini-2.5-flash | ~$0.10-0.30 |
| Judge reviews | claude-sonnet-4 | ~$0.50-2 |
| Audience panel (27 reviews) | gemini-2.5-flash | ~$2-8 |
| **Total estimate** | | **~$5-20 per full run** |

## Local Development

```bash
cd ~/slices/book-editor

# Start local Postgres + app
./author-tool-helper.sh local-up

# Or manually:
docker compose up -d
# API: http://localhost:8000
# DB:  postgresql://postgres:postgres@localhost:5433/book_editor

# Run tests
source .venv/bin/activate
pytest tests/ -x --tb=short
```

## Project Structure

```
~/slices/book-editor/
├── agent_system_prompts.json    ← All agent prompts (edit this!)
├── author-tool-helper.sh        ← Local CLI for everything
├── src/book_editor/
│   ├── main.py                  ← FastAPI app
│   ├── config.py                ← Settings (2 env vars)
│   ├── db.py                    ← PostgreSQL schema
│   ├── epub_parser.py           ← EPUB → markdown chapters
│   ├── llm.py                   ← OpenRouter client
│   ├── agents/
│   │   ├── editor.py            ← Chief Editor (1M context)
│   │   ├── stylist.py           ← Style Director
│   │   ├── judge.py             ← Quality Judge + RAG memory
│   │   ├── chapter_worker.py    ← Chapter revision workers
│   │   └── audience.py          ← Audience reviewer panel
│   └── pipelines/
│       ├── micro_book.py        ← Free dry-run pipeline
│       ├── full_book.py         ← Full 10-stage pipeline
│       └── orchestrator.py      ← Coordinates micro → full
├── Dockerfile                   ← Railway deployment
├── docker-compose.yml           ← Local development
└── tests/                       ← 12 passing tests
```
