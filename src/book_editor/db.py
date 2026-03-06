import asyncpg
import logging

from book_editor.config import settings

logger = logging.getLogger(__name__)

pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    source_filename TEXT,
    total_chapters INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chapters (
    id SERIAL PRIMARY KEY,
    book_id INT REFERENCES books(id) ON DELETE CASCADE,
    original_index INT NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    word_count INT DEFAULT 0,
    is_epilogue BOOLEAN DEFAULT FALSE,
    has_attributed_quotes BOOLEAN DEFAULT FALSE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chapter_revisions (
    id SERIAL PRIMARY KEY,
    chapter_id INT REFERENCES chapters(id) ON DELETE CASCADE,
    version INT NOT NULL,
    content TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    revision_notes TEXT,
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_interactions (
    id SERIAL PRIMARY KEY,
    book_id INT REFERENCES books(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    role TEXT NOT NULL,
    interaction_type TEXT NOT NULL,
    content TEXT NOT NULL,
    context JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS book_drafts (
    id SERIAL PRIMARY KEY,
    book_id INT REFERENCES books(id) ON DELETE CASCADE,
    version INT NOT NULL,
    chapter_order JSONB NOT NULL,
    first_chapter_content TEXT,
    last_chapter_content TEXT,
    assembly_notes TEXT,
    full_text TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audience_feedback (
    id SERIAL PRIMARY KEY,
    draft_id INT REFERENCES book_drafts(id) ON DELETE CASCADE,
    reviewer_name TEXT NOT NULL,
    reviewer_persona TEXT,
    round INT NOT NULL,
    positive_feedback TEXT,
    critical_feedback TEXT,
    overall_score INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS judge_memory (
    id SERIAL PRIMARY KEY,
    book_id INT REFERENCES books(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pipeline_status (
    id SERIAL PRIMARY KEY,
    book_id INT REFERENCES books(id) ON DELETE CASCADE,
    pipeline TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'queued',
    progress FLOAT DEFAULT 0.0,
    detail TEXT DEFAULT '',
    error TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS annotations (
    id SERIAL PRIMARY KEY,
    draft_id INT REFERENCES book_drafts(id) ON DELETE CASCADE,
    author_name TEXT NOT NULL DEFAULT '',
    selected_text TEXT NOT NULL,
    prefix_context TEXT DEFAULT '',
    suffix_context TEXT DEFAULT '',
    comment TEXT DEFAULT '',
    rating INT DEFAULT 0 CHECK (rating >= -2 AND rating <= 3),
    good_for_normies BOOLEAN DEFAULT FALSE,
    bad_for_normies BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_status_book ON pipeline_status(book_id, pipeline);
CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters(book_id, original_index);
CREATE INDEX IF NOT EXISTS idx_revisions_chapter ON chapter_revisions(chapter_id, version);
CREATE INDEX IF NOT EXISTS idx_interactions_book ON agent_interactions(book_id, created_at);
CREATE INDEX IF NOT EXISTS idx_judge_memory_book ON judge_memory(book_id, category);
CREATE INDEX IF NOT EXISTS idx_drafts_book ON book_drafts(book_id, version);
CREATE INDEX IF NOT EXISTS idx_annotations_draft ON annotations(draft_id);
"""


async def init_pool():
    global pool
    dsn = settings.database_url
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgres://", 1)
    pool = await asyncpg.create_pool(dsn, min_size=2, max_size=20)
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
    logger.info("Database pool initialized and schema applied")


async def close_pool():
    global pool
    if pool:
        await pool.close()
        pool = None


async def get_pool() -> asyncpg.Pool:
    if pool is None:
        await init_pool()
    return pool


async def update_pipeline_status(
    book_id: int, pipeline: str, stage: str, progress: float, detail: str = "", error: str | None = None
):
    """Upsert pipeline status into the database."""
    p = await get_pool()
    async with p.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM pipeline_status WHERE book_id = $1 AND pipeline = $2",
            book_id, pipeline,
        )
        if existing:
            await conn.execute(
                """UPDATE pipeline_status
                   SET stage = $1, progress = $2, detail = $3, error = $4, updated_at = NOW()
                   WHERE book_id = $5 AND pipeline = $6""",
                stage, progress, detail, error, book_id, pipeline,
            )
        else:
            await conn.execute(
                """INSERT INTO pipeline_status (book_id, pipeline, stage, progress, detail, error)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                book_id, pipeline, stage, progress, detail, error,
            )
    logger.info(f"Pipeline [{book_id}/{pipeline}] {stage}: {progress:.0%} {detail}" + (f" ERROR: {error}" if error else ""))


async def get_pipeline_status(book_id: int) -> list[dict]:
    """Get all pipeline statuses for a book."""
    p = await get_pool()
    async with p.acquire() as conn:
        rows = await conn.fetch(
            """SELECT pipeline, stage, progress, detail, error, started_at, updated_at
               FROM pipeline_status WHERE book_id = $1 ORDER BY started_at""",
            book_id,
        )
    return [dict(r) for r in rows]
