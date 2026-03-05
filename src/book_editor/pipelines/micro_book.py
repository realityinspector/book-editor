"""Micro-book pipeline: dry run with a free model producing a children's-book-level summary."""

import json
import logging

from book_editor import db
from book_editor.config import settings
from book_editor.llm import chat
from book_editor.agents.base import load_prompts

logger = logging.getLogger(__name__)


async def run_micro_book_pipeline(book_id: int) -> dict:
    """
    Run the micro-book dry run:
    1. Load all chapters
    2. Have a free model condense each into 1-3 sentences at second-grade level
    3. Produce a complete ~200 word micro-book
    4. Store the result

    This proves the pipeline works before attempting the expensive full run.
    """
    logger.info(f"Starting micro-book pipeline for book {book_id}")

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        book = await conn.fetchrow("SELECT title, author FROM books WHERE id = $1", book_id)
        chapters = await conn.fetch(
            """SELECT id, original_index, title, content
               FROM chapters WHERE book_id = $1 ORDER BY original_index""",
            book_id,
        )

    if not chapters:
        return {"error": "No chapters found"}

    prompts = load_prompts()
    micro_prompt = prompts["micro_book"]["system_prompt"]
    model = settings.micro_model

    # Build chapter summaries for the model
    chapter_list = "\n\n".join(
        f"CHAPTER {ch['original_index'] + 1}: {ch['title']}\n{ch['content'][:500]}..."
        for ch in chapters
    )

    messages = [
        {"role": "system", "content": micro_prompt},
        {
            "role": "user",
            "content": (
                f"Create a micro-book version of '{book['title']}' by {book['author']}.\n\n"
                f"The book has {len(chapters)} chapters. Here are their beginnings:\n\n"
                f"{chapter_list}\n\n"
                f"Create the micro-book: ~200 words total, second-grade reading level, "
                f"{len(chapters)} mini-chapters of 1-3 sentences each."
            ),
        },
    ]

    logger.info(f"Calling micro model: {model}")
    micro_text = await chat(model, messages, temperature=0.8, max_tokens=2000)

    # Store as a draft (version 0 = micro-book)
    async with pool.acquire() as conn:
        draft_id = await conn.fetchval(
            """INSERT INTO book_drafts (book_id, version, chapter_order, full_text, assembly_notes)
               VALUES ($1, 0, $2, $3, $4) RETURNING id""",
            book_id,
            json.dumps([{"chapter_id": ch["id"], "include": True, "position": i} for i, ch in enumerate(chapters)]),
            micro_text,
            "Micro-book dry run (second-grade level, ~200 words)",
        )

    logger.info(f"Micro-book complete: draft_id={draft_id}, {len(micro_text)} chars")

    return {
        "draft_id": draft_id,
        "book_id": book_id,
        "model_used": model,
        "micro_book_text": micro_text,
        "word_count": len(micro_text.split()),
        "status": "complete",
    }
