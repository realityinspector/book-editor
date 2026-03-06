"""Audience reviewer agents: roleplay different reader personas to review complete drafts."""

import json
import logging

from book_editor.agents.base import BaseAgent, load_prompts
from book_editor import db
from book_editor.llm import chat_json

logger = logging.getLogger(__name__)


class AudienceReviewerAgent(BaseAgent):
    agent_key = "audience_reviewer"

    def __init__(self, model: str, book_id: int, persona_name: str, persona_description: str):
        super().__init__(model, book_id)
        self.persona_name = persona_name
        self.persona_description = persona_description

        # Build persona-specific system prompt from template
        prompts = load_prompts()
        template = prompts["audience_reviewer"]["system_prompt_template"]
        # Use replace instead of .format() to avoid conflicts with JSON braces in template
        self.system_prompt = (
            template
            .replace("{persona_name}", persona_name)
            .replace("{persona_description}", persona_description)
        )

    async def review_draft(self, draft_id: int, round_num: int) -> dict:
        """Review a complete book draft. Returns structured feedback."""
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT full_text, version FROM book_drafts WHERE id = $1", draft_id
            )

        if not draft or not draft["full_text"]:
            return {"error": "Draft not found or empty"}

        full_text = draft["full_text"]

        # For very long texts, we may need to summarize or chunk
        # But with 1M context models this should be fine
        prompt = (
            f"You are reading version {draft['version']} of this book for round {round_num} of review.\n\n"
            f"COMPLETE BOOK:\n\n{full_text}\n\n"
            "Provide your complete review as the JSON format specified in your instructions."
        )

        response = await self.send(prompt)

        # Parse feedback
        feedback = self._parse_feedback(response)

        # Store in DB
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO audience_feedback
                   (draft_id, reviewer_name, reviewer_persona, round, positive_feedback, critical_feedback, overall_score)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                draft_id,
                self.persona_name,
                self.persona_description,
                round_num,
                json.dumps(feedback.get("positive", [])),
                json.dumps(feedback.get("critical", [])),
                feedback.get("overall_score", 5),
            )

        logger.info(
            f"Audience '{self.persona_name}' reviewed draft {draft_id} round {round_num}: "
            f"score={feedback.get('overall_score', '?')}/10"
        )
        return feedback

    def _parse_feedback(self, response: str) -> dict:
        """Extract structured feedback from response."""
        import re
        # Try to find JSON in the response
        json_match = re.search(r'\{[^{}]*"positive"\s*:', response, re.DOTALL)
        if json_match:
            # Find the matching closing brace
            start = json_match.start()
            depth = 0
            for i, c in enumerate(response[start:], start):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(response[start:i + 1])
                        except json.JSONDecodeError:
                            break

        # Fallback: return raw response as feedback
        return {
            "positive": [response[:500]],
            "critical": ["Could not parse structured feedback"],
            "overall_score": 5,
            "would_recommend": True,
            "one_line_review": "Review provided in unstructured format",
        }


def create_audience_panel(model: str, book_id: int) -> list[AudienceReviewerAgent]:
    """Create the three audience reviewer agents from the prompts config."""
    prompts = load_prompts()
    personas = prompts.get("audience_personas", [])

    return [
        AudienceReviewerAgent(
            model=model,
            book_id=book_id,
            persona_name=p["name"],
            persona_description=p["description"],
        )
        for p in personas
    ]
