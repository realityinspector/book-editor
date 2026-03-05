"""Chapter worker agent: edits individual chapters under editor direction."""

import json
import logging
import re

from book_editor.agents.base import BaseAgent
from book_editor import db

logger = logging.getLogger(__name__)


class ChapterWorkerAgent(BaseAgent):
    agent_key = "chapter_worker"

    def __init__(self, model: str, book_id: int, worker_id: int):
        super().__init__(model, book_id)
        self.worker_id = worker_id

    async def revise_chapter(
        self,
        chapter_id: int,
        original_content: str,
        editor_instructions: str,
        style_brief: str,
        is_epilogue: bool = False,
    ) -> dict:
        """
        Revise a single chapter. Returns {content, revision_notes, preserved_quotes, confidence}.
        """
        if is_epilogue:
            logger.info(f"Worker {self.worker_id}: skipping epilogue chapter {chapter_id}")
            return {
                "content": original_content,
                "revision_notes": "Epilogue — preserved unchanged per editorial policy",
                "preserved_quotes": [],
                "confidence": 1.0,
            }

        prompt = (
            f"REVISE this chapter following the instructions below.\n\n"
            f"STYLE BRIEF:\n{style_brief}\n\n"
            f"EDITOR'S INSTRUCTIONS:\n{editor_instructions}\n\n"
            f"ORIGINAL CHAPTER:\n{original_content}\n\n"
            "Write the COMPLETE revised chapter, then provide your metadata JSON at the end."
        )

        response = await self.send(prompt, max_tokens=8000)

        # Parse response: extract the revised content and metadata
        content, metadata = self._parse_revision_response(response, original_content)

        # Store the revision
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            version = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM chapter_revisions WHERE chapter_id = $1",
                chapter_id,
            )
            await conn.execute(
                """INSERT INTO chapter_revisions (chapter_id, version, content, agent_name, revision_notes, status)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                chapter_id,
                version,
                content,
                f"worker_{self.worker_id}",
                metadata.get("revision_notes", ""),
                "judge_review",
            )

        logger.info(f"Worker {self.worker_id}: revised chapter {chapter_id} v{version} ({len(content)} chars)")

        return {
            "content": content,
            "revision_notes": metadata.get("revision_notes", ""),
            "preserved_quotes": metadata.get("preserved_quotes", []),
            "confidence": metadata.get("confidence", 0.5),
            "version": version,
        }

    def _parse_revision_response(self, response: str, original: str) -> tuple[str, dict]:
        """Separate revised content from metadata JSON."""
        metadata = {}

        # Try to find JSON metadata block at end
        json_match = re.search(r'\{[^{}]*"revision_notes"\s*:[^{}]*\}', response, re.DOTALL)
        if json_match:
            try:
                metadata = json.loads(json_match.group())
                # Content is everything before the JSON
                content = response[:json_match.start()].strip()
            except json.JSONDecodeError:
                content = response.strip()
        else:
            content = response.strip()

        # Sanity check: if content is too short, something went wrong
        if len(content) < len(original) * 0.3:
            logger.warning(f"Worker {self.worker_id}: revision suspiciously short, using full response")
            content = response.strip()

        return content, metadata
