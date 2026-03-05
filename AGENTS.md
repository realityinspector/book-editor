# Book Editor — AI Agent Architecture

## Overview

Multi-agent book revision pipeline. Ingests an .epub, uses distributed AI agents via OpenRouter to debate, revise, and reassemble the book into three variant editions reviewed by an audience panel.

## Agent Hierarchy

```
                    ┌─────────────┐
                    │   EDITOR    │  google/gemini-2.5-pro (1M context)
                    │  (vision)   │  Reads entire book, structural decisions
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
       ┌──────┴──────┐ ┌──┴───┐ ┌──────┴──────┐
       │  STYLIST    │ │JUDGE │ │  AUDIENCE    │
       │  (voice)    │ │(gate)│ │  (feedback)  │
       └──────┬──────┘ └──┬───┘ └─────────────┘
              │           │
              │     ┌─────┴─────┐
              │     │  WORKERS  │  (swarm, concurrent)
              │     │ ch1 ch2.. │
              └─────┴───────────┘
```

## Agent Roles

| Agent | Model | Context | Role |
|-------|-------|---------|------|
| **Editor** | gemini-2.5-pro | 1M | Reads entire book. Develops vision. Orders chapters. Writes variant first chapters. Assembles drafts. |
| **Stylist** | gemini-2.5-flash | 1M | Analyzes author's voice. Debates editor on structure vs style. Produces style brief for workers. |
| **Judge** | claude-sonnet-4 | 200K | Reviews every revision. Approves/rejects. Maintains RAG memory. Coordinates with workers. |
| **Workers** | gemini-2.5-flash | 1M | Swarm of chapter editors. Follow editor instructions + style brief. Submit to judge. |
| **Audience** | gemini-2.5-flash | 1M | 3 personas (Jordan, Maya, Robert) review complete drafts. 3 rounds each. |
| **Micro** | llama-3.3-70b:free | 128K | Free model for dry-run. Produces children's-book-level summary. |

## Pipeline Stages

1. **Ingest** — Parse .epub → chapters as markdown → PostgreSQL
2. **Micro dry run** — Free model produces ~200 word children's book version (proves pipeline works)
3. **Editor reads** — Entire book loaded into 1M context, produces structural assessment
4. **Voice analysis** — Stylist analyzes author's distinctive style
5. **Debate** — 3 rounds of Editor ↔ Stylist debate on architecture vs voice
6. **Style brief** — Stylist produces actionable guidelines for workers
7. **Chapter instructions** — Editor generates per-chapter revision directives
8. **Worker swarm** — Concurrent chapter editing with Judge approve/reject loop (max 3 attempts)
9. **Chapter ordering** — Editor determines final order, can exclude chapters
10. **Variant assembly** — 3 versions with different first chapters and chapter orders
11. **Audience review** — 3 personas × 3 drafts × 3 rounds = 27 reviews
12. **Report** — Aggregated scores and feedback

## Sacred Rules

- **Attributed quotations** from other authors/historical sources are NEVER modified
- **The epilogue** for the author's children remains UNTOUCHED
- The author's prose style is LOVED by the stylist — architecture is the problem, not the writing
- The author's thinking pattern is an "autistic painting of a logic tree" — may need complete architectural rethinking

## Configuration

- `agent_system_prompts.json` — All agent system prompts (single file, easy to update)
- `.env` — Only needs `DATABASE_URL` and `OPENROUTER_API_KEY`
- `config.py` — Model assignments, concurrency limits

## Local Development

```bash
# Start local env
./author-tool-helper.sh local-up

# Upload a book
./author-tool-helper.sh upload my-book.epub

# Run micro dry run first
./author-tool-helper.sh micro 1

# Run full pipeline
./author-tool-helper.sh full 1

# Watch progress
./author-tool-helper.sh watch 1
```

## Railway Deployment

```bash
./author-tool-helper.sh setup    # One-time Railway setup
./author-tool-helper.sh deploy   # Deploy
./author-tool-helper.sh logs     # Monitor
```
