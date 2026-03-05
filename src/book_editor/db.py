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

CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters(book_id, original_index);
CREATE INDEX IF NOT EXISTS idx_revisions_chapter ON chapter_revisions(chapter_id, version);
CREATE INDEX IF NOT EXISTS idx_interactions_book ON agent_interactions(book_id, created_at);
CREATE INDEX IF NOT EXISTS idx_judge_memory_book ON judge_memory(book_id, category);
CREATE INDEX IF NOT EXISTS idx_drafts_book ON book_drafts(book_id, version);
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
