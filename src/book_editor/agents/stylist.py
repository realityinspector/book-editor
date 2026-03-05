"""Stylist agent: champions the author's voice, debates the editor on structure vs style."""

import logging

from book_editor.agents.base import BaseAgent
from book_editor import db

logger = logging.getLogger(__name__)


class StylistAgent(BaseAgent):
    agent_key = "stylist"

    async def analyze_voice(self, sample_chapters: list[dict]) -> str:
        """Analyze the author's writing voice from sample chapters."""
        samples = "\n\n---\n\n".join(
            f"Chapter: {ch['title']}\n{ch['content'][:2000]}" for ch in sample_chapters
        )

        return await self.send(
            f"Analyze the author's writing voice from these sample chapters:\n\n{samples}\n\n"
            "Identify:\n"
            "1. Distinctive sentence patterns and rhythms\n"
            "2. Vocabulary choices and register\n"
            "3. How they build arguments (the 'logic tree' style)\n"
            "4. Emotional tone and personal voice\n"
            "5. What works beautifully vs what creates reader barriers\n"
            "6. Your style guidelines for any worker editing this book"
        )

    async def debate_with_editor(self, editor_assessment: str) -> str:
        """Respond to the editor's structural assessment, advocating for voice preservation."""
        return await self.send(
            f"The Editor has provided this assessment of the book:\n\n{editor_assessment}\n\n"
            "Respond as the Stylist. Where does the Editor's structural vision threaten "
            "the author's voice? Where do you agree restructuring is needed? "
            "What are your non-negotiables for preserving the prose? "
            "Propose specific compromises where structure and style can both win."
        )

    async def review_chapter_voice(self, original: str, revised: str) -> str:
        """Check if a revision maintains the author's voice."""
        return await self.send(
            f"Compare the original and revised chapter for voice consistency.\n\n"
            f"ORIGINAL (excerpt):\n{original[:1500]}\n\n"
            f"REVISED (excerpt):\n{revised[:1500]}\n\n"
            "Does the revision maintain the author's voice? Rate voice preservation 1-10. "
            "Flag any passages where the voice has been flattened or lost."
        )

    async def provide_style_brief(self) -> str:
        """Generate a style brief for chapter workers to follow."""
        return await self.send(
            "Based on your analysis of the author's voice, write a concise STYLE BRIEF "
            "that chapter revision workers should follow. Include:\n"
            "1. DO: specific things to preserve or amplify\n"
            "2. DON'T: specific things to avoid\n"
            "3. VOICE MARKERS: distinctive patterns to maintain\n"
            "4. AUDIENCE NOTES: what the target readers need\n"
            "Keep it under 500 words — workers need this to be actionable."
        )
