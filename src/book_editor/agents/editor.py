"""Editor agent: holds the entire book in context, makes structural decisions."""

import json
import logging

from book_editor.agents.base import BaseAgent
from book_editor import db

logger = logging.getLogger(__name__)


class EditorAgent(BaseAgent):
    agent_key = "editor"

    async def read_entire_book(self) -> str:
        """Load and read the entire book. Returns the full text sent to the editor."""
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            chapters = await conn.fetch(
                """SELECT id, original_index, title, content, word_count, is_epilogue, has_attributed_quotes
                   FROM chapters WHERE book_id = $1 ORDER BY original_index""",
                self.book_id,
            )

        # Build full book text
        parts = []
        total_words = 0
        for ch in chapters:
            marker = ""
            if ch["is_epilogue"]:
                marker = " [EPILOGUE - DO NOT MODIFY]"
            if ch["has_attributed_quotes"]:
                marker += " [CONTAINS ATTRIBUTED QUOTES - PRESERVE EXACTLY]"

            parts.append(f"--- CHAPTER {ch['original_index'] + 1}: {ch['title']}{marker} (ID: {ch['id']}) ---\n\n{ch['content']}")
            total_words += ch["word_count"]

        full_text = "\n\n\n".join(parts)
        logger.info(f"Editor reading full book: {len(chapters)} chapters, {total_words} words")

        # Send to editor for initial reading
        reading_prompt = f"""Here is the COMPLETE book ({len(chapters)} chapters, ~{total_words} words). Read it carefully and develop your editorial vision.

{full_text}

After reading, provide your initial editorial assessment as JSON:
{{
    "overall_impression": "your honest assessment",
    "core_thesis": "what is this book really about",
    "structural_problems": ["problem1", "problem2", ...],
    "strongest_chapters": [list of chapter IDs],
    "weakest_chapters": [list of chapter IDs],
    "recommended_order_changes": "description of reordering if needed",
    "chapters_to_consider_cutting": [list of chapter IDs],
    "key_themes": ["theme1", "theme2", ...],
    "target_audience_notes": "who should read this and what do they need"
}}"""

        assessment = await self.send_json(reading_prompt)
        logger.info(f"Editor assessment complete: {len(assessment.get('structural_problems', []))} problems identified")
        return assessment

    async def debate_with_stylist(self, stylist_position: str) -> str:
        """Engage in debate with the stylist about the book's direction."""
        return await self.send(
            f"The Stylist has this position on the book:\n\n{stylist_position}\n\n"
            "Respond to their points. Where do you agree? Where do you disagree? "
            "Be specific about chapters and passages. What is your counter-argument "
            "on structure vs. voice preservation?"
        )

    async def generate_chapter_instructions(self, chapter_id: int, chapter_content: str, chapter_title: str) -> str:
        """Generate specific revision instructions for a chapter worker."""
        return await self.send(
            f"Generate specific revision instructions for Chapter '{chapter_title}' (ID: {chapter_id}).\n\n"
            f"Current content:\n{chapter_content[:3000]}{'...[truncated]' if len(chapter_content) > 3000 else ''}\n\n"
            "Provide detailed, actionable instructions for the chapter worker. "
            "What should change? What must be preserved? What's the goal for this chapter "
            "in the context of the whole book?"
        )

    async def review_revision(self, chapter_id: int, original: str, revised: str) -> dict:
        """Review a chapter revision and decide if it meets the vision."""
        return await self.send_json(
            f"Review this chapter revision (Chapter ID: {chapter_id}).\n\n"
            f"ORIGINAL (first 2000 chars):\n{original[:2000]}\n\n"
            f"REVISED (first 2000 chars):\n{revised[:2000]}\n\n"
            "Does this revision serve the book's overall vision? Respond as JSON:\n"
            '{"approved": true/false, "feedback": "specific feedback", "needs_another_pass": true/false}'
        )

    async def determine_chapter_order(self) -> dict:
        """Ask the editor to determine final chapter ordering and exclusions."""
        return await self.send_json(
            "Based on everything we've discussed and all the revisions completed, "
            "provide the FINAL chapter ordering for the book. You may exclude chapters entirely. "
            "Remember: the epilogue for the author's children stays as the final chapter, untouched.\n\n"
            "Respond with the JSON structure from your system prompt for chapter ordering."
        )

    async def write_variant_first_chapter(self, variant_number: int, chapter_order: list) -> str:
        """Write a new first chapter for a specific variant of the book."""
        order_desc = ", ".join([f"Ch{c['chapter_id']}" for c in chapter_order[:5]])
        return await self.send(
            f"Write a NEW first chapter for Variant {variant_number} of the book.\n"
            f"The chapter sequence for this variant starts: {order_desc}...\n\n"
            f"This first chapter needs to hook the reader and set up what follows. "
            f"Variant {variant_number} emphasizes a different entry point into the book's ideas. "
            f"Write the complete chapter in the author's voice."
        )

    async def assemble_draft(self, version: int, chapter_order: list[dict]) -> int:
        """Assemble a complete book draft from approved revisions. Returns draft_id."""
        pool = await db.get_pool()
        parts = []

        async with pool.acquire() as conn:
            for entry in chapter_order:
                if not entry.get("include", True):
                    continue
                ch_id = entry["chapter_id"]
                # Get latest approved revision, or original if none
                rev = await conn.fetchrow(
                    """SELECT content FROM chapter_revisions
                       WHERE chapter_id = $1 AND status = 'approved'
                       ORDER BY version DESC LIMIT 1""",
                    ch_id,
                )
                if rev:
                    parts.append(rev["content"])
                else:
                    orig = await conn.fetchrow(
                        "SELECT content FROM chapters WHERE id = $1", ch_id
                    )
                    if orig:
                        parts.append(orig["content"])

            full_text = "\n\n---\n\n".join(parts)

            draft_id = await conn.fetchval(
                """INSERT INTO book_drafts (book_id, version, chapter_order, full_text, assembly_notes)
                   VALUES ($1, $2, $3, $4, $5) RETURNING id""",
                self.book_id,
                version,
                json.dumps(chapter_order),
                full_text,
                f"Assembled variant {version}",
            )

        logger.info(f"Assembled draft {draft_id} version {version}: {len(parts)} chapters, {len(full_text)} chars")
        return draft_id
