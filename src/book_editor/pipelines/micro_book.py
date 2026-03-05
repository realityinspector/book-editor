"""Micro-book pipeline: dry run with a cheap model producing a children's-book-level summary."""

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
    2. Send chapter titles + first ~200 chars to a cheap model
    3. Get back a ~200 word children's-book-level summary
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
        return {"error": "No chapters found", "status": "failed"}

    prompts = load_prompts()
    micro_prompt = prompts["micro_book"]["system_prompt"]
    model = settings.micro_model

    # Build a lightweight chapter list — just titles and a one-line hint
    chapter_lines = []
    for ch in chapters:
        # First sentence or first 150 chars as a hint
        content_hint = ch["content"][:150].split(".")[0].strip()
        chapter_lines.append(f"  {ch['original_index'] + 1}. \"{ch['title']}\" — {content_hint}")

    chapter_list = "\n".join(chapter_lines)

    messages = [
        {"role": "system", "content": micro_prompt},
        {
            "role": "user",
            "content": (
                f"Book: \"{book['title']}\" by {book['author']}\n"
                f"Total chapters: {len(chapters)}\n\n"
                f"Chapter list:\n{chapter_list}\n\n"
                f"Now write the complete micro-book. Remember:\n"
                f"- About 200 words total across all {len(chapters)} chapters\n"
                f"- Each chapter is 1-3 simple sentences at a second-grade reading level\n"
                f"- Use the format: ## [Chapter Title] followed by the simple sentences\n"
                f"- End with a one-sentence summary of the whole book\n\n"
                f"Write the micro-book now:"
            ),
        },
    ]

    logger.info(f"Calling micro model: {model}")
    micro_text = await chat(model, messages, temperature=0.8, max_tokens=4000)

    if not micro_text or len(micro_text.split()) < 20:
        logger.error(f"Micro-book too short: {len(micro_text.split())} words. Raw: {micro_text!r}")
        return {
            "error": f"Model returned insufficient output ({len(micro_text.split())} words): {micro_text!r}",
            "status": "failed",
        }

    # Store as a draft (version 0 = micro-book)
    async with pool.acquire() as conn:
        # Delete any existing micro draft for this book
        await conn.execute(
            "DELETE FROM book_drafts WHERE book_id = $1 AND version = 0", book_id
        )

        draft_id = await conn.fetchval(
            """INSERT INTO book_drafts (book_id, version, chapter_order, full_text, assembly_notes)
               VALUES ($1, 0, $2, $3, $4) RETURNING id""",
            book_id,
            json.dumps([{"chapter_id": ch["id"], "include": True, "position": i} for i, ch in enumerate(chapters)]),
            micro_text,
            "Micro-book dry run (second-grade level, ~200 words)",
        )

    word_count = len(micro_text.split())
    logger.info(f"Micro-book complete: draft_id={draft_id}, {word_count} words, {len(micro_text)} chars")

    return {
        "draft_id": draft_id,
        "book_id": book_id,
        "model_used": model,
        "micro_book_text": micro_text,
        "word_count": word_count,
        "status": "complete",
    }
