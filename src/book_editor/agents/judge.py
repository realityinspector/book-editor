"""Judge agent: validates revisions, maintains memory/RAG across the editing process."""

import json
import logging

from book_editor.agents.base import BaseAgent
from book_editor import db

logger = logging.getLogger(__name__)


class JudgeAgent(BaseAgent):
    agent_key = "judge"

    async def load_memory(self):
        """Load accumulated memory from the database into conversation context."""
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            memories = await conn.fetch(
                """SELECT category, key, value FROM judge_memory
                   WHERE book_id = $1 ORDER BY created_at""",
                self.book_id,
            )

        if memories:
            memory_text = "Your accumulated editorial memory:\n\n"
            for m in memories:
                memory_text += f"[{m['category']}] {m['key']}: {m['value']}\n"

            # Inject as first user/assistant exchange so it's in context
            self.conversation = [
                {"role": "user", "content": "Load your editorial memory."},
                {"role": "assistant", "content": memory_text},
            ]
            logger.info(f"Judge loaded {len(memories)} memory items")

    async def _save_memories(self, response: str):
        """Extract and save any memory items from the judge's response."""
        try:
            # Look for memory JSON blocks in the response
            if '"memory"' not in response:
                return

            # Try to extract memory objects
            import re
            pattern = r'\{[^{}]*"memory"\s*:\s*\{[^{}]*\}[^{}]*\}'
            matches = re.findall(pattern, response)

            pool = await db.get_pool()
            async with pool.acquire() as conn:
                for match in matches:
                    try:
                        data = json.loads(match)
                        mem = data.get("memory", {})
                        if mem.get("key") and mem.get("value"):
                            await conn.execute(
                                """INSERT INTO judge_memory (book_id, category, key, value, source_agent)
                                   VALUES ($1, $2, $3, $4, $5)""",
                                self.book_id,
                                mem.get("category", "general"),
                                mem["key"],
                                mem["value"],
                                "judge",
                            )
                            logger.info(f"Judge saved memory: [{mem.get('category')}] {mem['key']}")
                    except (json.JSONDecodeError, KeyError):
                        continue
        except Exception as e:
            logger.warning(f"Memory extraction failed: {e}")

    async def judge_revision(
        self,
        chapter_id: int,
        original_content: str,
        revised_content: str,
        revision_notes: str,
        style_brief: str,
        editor_instructions: str,
    ) -> dict:
        """Judge a chapter revision. Returns approval/rejection with reasoning."""
        await self.load_memory()

        prompt = (
            f"JUDGE this chapter revision (Chapter ID: {chapter_id}).\n\n"
            f"STYLE BRIEF from Stylist:\n{style_brief}\n\n"
            f"EDITOR INSTRUCTIONS:\n{editor_instructions}\n\n"
            f"ORIGINAL CHAPTER:\n{original_content}\n\n"
            f"REVISED CHAPTER:\n{revised_content}\n\n"
            f"WORKER NOTES: {revision_notes}\n\n"
            "Evaluate against all criteria in your system prompt. "
            "Respond with your judgment JSON (approved/rejected). "
            "Also include any memory items you want to save for future judgments."
        )

        response = await self.send(prompt)
        await self._save_memories(response)

        # Parse the judgment
        try:
            # Extract JSON from response
            import re
            json_match = re.search(r'\{[^{}]*"decision"\s*:[^{}]*\}', response)
            if json_match:
                return json.loads(json_match.group())
        except (json.JSONDecodeError, AttributeError):
            pass

        # Fallback: treat as rejected if we can't parse
        logger.warning(f"Could not parse judge response for chapter {chapter_id}, treating as needs review")
        return {"decision": "rejected", "chapter_id": chapter_id, "issues": ["Could not parse judge response"], "guidance": response[:500]}

    async def coordinate_with_worker(self, worker_name: str, question: str) -> str:
        """Handle a coordination request from a worker agent."""
        response = await self.send(
            f"Worker '{worker_name}' has a question during their revision:\n\n{question}\n\n"
            "Provide guidance based on your accumulated editorial knowledge."
        )
        await self._save_memories(response)
        return response
