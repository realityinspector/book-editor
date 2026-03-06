"""FastAPI entry point for the book editing pipeline."""

import asyncio
import json
import logging
import os
import shutil
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from book_editor import db
from book_editor.config import settings
from book_editor.pipelines.orchestrator import run_pipeline
from book_editor.pipelines.full_book import run_full_book_pipeline
from book_editor.pipelines.micro_book import run_micro_book_pipeline
from book_editor.epub_parser import ingest_epub
from book_editor.browser import router as browser_router, _get_current_user

# ── Logging ──

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Try to set up Logfire if available and configured
try:
    import logfire
    logfire.configure(service_name="book-editor")
    logfire.instrument_asyncpg()
    logger.info("Logfire configured")
except Exception:
    logger.info("Logfire not available or not configured, using standard logging")

UPLOAD_DIR = Path("/app/uploads") if os.path.exists("/app") else Path("./uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    logger.info("Book Editor API started")
    yield
    await db.close_pool()
    logger.info("Book Editor API stopped")


app = FastAPI(
    title="Book Editor Pipeline",
    description="AI-powered book revision pipeline using distributed agents",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount browser UI (serves /, /browse, /browse/*, /auth, /logout)
app.include_router(browser_router)


# ── Health ──

@app.get("/health")
async def health():
    return {"status": "ok", "service": "book-editor"}


# ── Upload & Ingest ──

@app.post("/books/upload")
async def upload_epub(request: Request, file: UploadFile = File(...)):
    """Upload an .epub file and ingest it into the database."""
    if not file.filename or not file.filename.endswith(".epub"):
        raise HTTPException(400, "File must be an .epub")

    filepath = UPLOAD_DIR / file.filename
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    book_id = await ingest_epub(str(filepath))

    # Set owner if user is authenticated
    user = await _get_current_user(request)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        if user and user["id"] > 0:
            await conn.execute("UPDATE books SET owner_id = $1 WHERE id = $2", user["id"], book_id)
        book = await conn.fetchrow("SELECT * FROM books WHERE id = $1", book_id)
        chapter_count = await conn.fetchval(
            "SELECT COUNT(*) FROM chapters WHERE book_id = $1", book_id
        )

    return {
        "book_id": book_id,
        "title": book["title"],
        "author": book["author"],
        "chapters": chapter_count,
    }


# ── Pipeline Execution ──

@app.post("/books/{book_id}/micro")
async def start_micro_pipeline(book_id: int):
    """Run the micro-book dry run pipeline."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM books WHERE id = $1", book_id)
    if not exists:
        raise HTTPException(404, "Book not found")

    await db.update_pipeline_status(book_id, "micro", "queued", 0.0, "Micro pipeline queued")
    asyncio.create_task(_run_micro(book_id))
    return {"status": "started", "book_id": book_id, "pipeline": "micro_book"}


async def _run_micro(book_id: int):
    try:
        await db.update_pipeline_status(book_id, "micro", "running", 0.1, "Calling free model...")
        result = await run_micro_book_pipeline(book_id)
        if result.get("status") == "complete":
            await db.update_pipeline_status(
                book_id, "micro", "complete", 1.0,
                f"Micro-book done: {result.get('word_count', 0)} words, draft_id={result.get('draft_id')}"
            )
        else:
            await db.update_pipeline_status(
                book_id, "micro", "failed", 0.0,
                error=result.get("error", "Unknown error")
            )
    except Exception as e:
        tb = traceback.format_exc()
        logger.exception(f"Micro pipeline failed for book {book_id}")
        await db.update_pipeline_status(book_id, "micro", "FAILED", 0.0, error=f"{e}\n\n{tb}")


@app.post("/books/{book_id}/full")
async def start_full_pipeline(book_id: int):
    """Run the full book editing pipeline."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM books WHERE id = $1", book_id)
    if not exists:
        raise HTTPException(404, "Book not found")

    await db.update_pipeline_status(book_id, "full", "queued", 0.0, "Full pipeline queued")
    asyncio.create_task(_run_full(book_id))
    return {"status": "started", "book_id": book_id, "pipeline": "full_book"}


async def _run_full(book_id: int):
    try:
        await db.update_pipeline_status(book_id, "full", "running", 0.05, "Starting full pipeline...")
        result = await run_full_book_pipeline(book_id)
        status = result.get("status", "unknown")
        if status == "complete":
            summary = result.get("summary", {})
            await db.update_pipeline_status(
                book_id, "full", "complete", 1.0,
                detail=f"Done. Scores: {json.dumps(summary)}"
            )
        else:
            await db.update_pipeline_status(
                book_id, "full", "FAILED", 0.0,
                error=result.get("error", "Pipeline returned non-complete status")
            )
    except Exception as e:
        tb = traceback.format_exc()
        logger.exception(f"Full pipeline failed for book {book_id}")
        await db.update_pipeline_status(book_id, "full", "FAILED", 0.0, error=f"{e}\n\n{tb}")


@app.post("/books/{book_id}/run-all")
async def start_full_orchestration(book_id: int, skip_micro: bool = False):
    """Run micro dry run then full pipeline. Requires book already ingested."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM books WHERE id = $1", book_id)
    if not exists:
        raise HTTPException(404, "Book not found")

    await db.update_pipeline_status(book_id, "orchestration", "queued", 0.0, "Full orchestration queued")
    asyncio.create_task(_run_all(book_id, skip_micro))
    return {"status": "started", "book_id": book_id, "pipeline": "full_orchestration", "skip_micro": skip_micro}


async def _run_all(book_id: int, skip_micro: bool):
    try:
        if not skip_micro:
            await db.update_pipeline_status(book_id, "orchestration", "micro", 0.1, "Running micro dry run...")
            micro = await run_micro_book_pipeline(book_id)
            if micro.get("status") != "complete":
                await db.update_pipeline_status(
                    book_id, "orchestration", "FAILED", 0.0,
                    error=f"Micro dry run failed: {micro.get('error', 'unknown')}"
                )
                return
            await db.update_pipeline_status(book_id, "orchestration", "micro_done", 0.2, "Micro done, starting full...")

        await db.update_pipeline_status(book_id, "orchestration", "full", 0.25, "Running full pipeline...")
        result = await run_full_book_pipeline(book_id)
        status = result.get("status", "unknown")
        if status == "complete":
            await db.update_pipeline_status(book_id, "orchestration", "complete", 1.0, "All done")
        else:
            await db.update_pipeline_status(
                book_id, "orchestration", "FAILED", 0.0,
                error=result.get("error", "Full pipeline returned non-complete status")
            )
    except Exception as e:
        tb = traceback.format_exc()
        logger.exception(f"Orchestration failed for book {book_id}")
        await db.update_pipeline_status(book_id, "orchestration", "FAILED", 0.0, error=f"{e}\n\n{tb}")


# ── Delete ──

@app.delete("/books/{book_id}")
async def delete_book(book_id: int):
    """Delete a book and all its associated data (chapters, revisions, drafts, feedback, interactions, memory)."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM books WHERE id = $1", book_id)
        if not exists:
            raise HTTPException(404, "Book not found")
        await conn.execute("DELETE FROM pipeline_status WHERE book_id = $1", book_id)
        await conn.execute("DELETE FROM judge_memory WHERE book_id = $1", book_id)
        await conn.execute("DELETE FROM agent_interactions WHERE book_id = $1", book_id)
        await conn.execute("DELETE FROM books WHERE id = $1", book_id)
    return {"status": "deleted", "book_id": book_id}


# ── Status & Results ──

@app.get("/books/{book_id}/status")
async def pipeline_status(book_id: int):
    """Get current pipeline status for a book. Shows ALL pipelines with errors."""
    statuses = await db.get_pipeline_status(book_id)
    if not statuses:
        return {"book_id": book_id, "pipelines": [], "summary": "No pipelines started yet"}

    has_error = any(s.get("error") for s in statuses)
    has_running = any(s["stage"] not in ("complete", "FAILED", "queued") for s in statuses)

    summary = "ERROR — check 'error' fields below" if has_error else "running" if has_running else "idle"
    return {"book_id": book_id, "pipelines": statuses, "summary": summary}


@app.get("/books/{book_id}")
async def get_book(book_id: int):
    """Get book metadata and chapter list."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        book = await conn.fetchrow("SELECT * FROM books WHERE id = $1", book_id)
        if not book:
            raise HTTPException(404, "Book not found")
        chapters = await conn.fetch(
            """SELECT id, original_index, title, word_count, is_epilogue, has_attributed_quotes
               FROM chapters WHERE book_id = $1 ORDER BY original_index""",
            book_id,
        )
    return {
        "book": dict(book),
        "chapters": [dict(ch) for ch in chapters],
    }


@app.get("/books/{book_id}/drafts")
async def get_drafts(book_id: int):
    """Get all assembled drafts for a book."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        drafts = await conn.fetch(
            """SELECT id, version, assembly_notes, created_at,
                      LENGTH(full_text) as text_length
               FROM book_drafts WHERE book_id = $1 ORDER BY version""",
            book_id,
        )
    return {"drafts": [dict(d) for d in drafts]}


@app.get("/drafts/{draft_id}")
async def get_draft(draft_id: int):
    """Get a specific draft with full text."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        draft = await conn.fetchrow(
            "SELECT * FROM book_drafts WHERE id = $1", draft_id
        )
        if not draft:
            raise HTTPException(404, "Draft not found")
        feedback = await conn.fetch(
            """SELECT reviewer_name, round, positive_feedback, critical_feedback, overall_score
               FROM audience_feedback WHERE draft_id = $1 ORDER BY round, reviewer_name""",
            draft_id,
        )
    return {
        "draft": dict(draft),
        "feedback": [dict(f) for f in feedback],
    }


@app.get("/books/{book_id}/interactions")
async def get_interactions(book_id: int, limit: int = 50):
    """Get agent interaction log."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT agent_name, role, interaction_type, content, created_at
               FROM agent_interactions WHERE book_id = $1
               ORDER BY created_at DESC LIMIT $2""",
            book_id,
            limit,
        )
    return {"interactions": [dict(r) for r in rows]}


@app.get("/books/{book_id}/judge-memory")
async def get_judge_memory(book_id: int):
    """Get the judge's accumulated memory."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT category, key, value, source_agent, created_at
               FROM judge_memory WHERE book_id = $1 ORDER BY created_at""",
            book_id,
        )
    return {"memories": [dict(r) for r in rows]}
