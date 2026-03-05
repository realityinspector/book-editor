"""Base agent with OpenRouter integration and interaction logging."""

import json
import logging
from pathlib import Path

from book_editor import db
from book_editor.llm import chat, chat_json

logger = logging.getLogger(__name__)

PROMPTS_PATH = Path(__file__).parent.parent.parent.parent / "agent_system_prompts.json"


def load_prompts() -> dict:
    """Load agent system prompts from the JSON config file."""
    with open(PROMPTS_PATH) as f:
        return json.load(f)


class BaseAgent:
    """Base class for all agents in the book editing pipeline."""

    agent_key: str = ""  # Override in subclass
    role: str = ""

    def __init__(self, model: str, book_id: int):
        self.model = model
        self.book_id = book_id
        self.conversation: list[dict] = []
        prompts = load_prompts()
        agent_config = prompts.get(self.agent_key, {})
        self.system_prompt = agent_config.get("system_prompt", "")
        self.role = agent_config.get("role", self.agent_key)

    def _build_messages(self, user_message: str) -> list[dict]:
        """Build the full message list with system prompt and conversation history."""
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.conversation)
        messages.append({"role": "user", "content": user_message})
        return messages

    async def send(self, message: str, temperature: float = 0.7, max_tokens: int | None = None) -> str:
        """Send a message and get a response. Logs the interaction."""
        messages = self._build_messages(message)
        response = await chat(self.model, messages, temperature=temperature, max_tokens=max_tokens)

        # Track conversation
        self.conversation.append({"role": "user", "content": message})
        self.conversation.append({"role": "assistant", "content": response})

        # Log to DB
        await self._log_interaction("message", message, response)

        return response

    async def send_json(self, message: str, temperature: float = 0.4, max_tokens: int | None = None) -> dict:
        """Send a message expecting JSON response."""
        messages = self._build_messages(message)
        response = await chat_json(self.model, messages, temperature=temperature, max_tokens=max_tokens)

        self.conversation.append({"role": "user", "content": message})
        self.conversation.append({"role": "assistant", "content": json.dumps(response)})

        await self._log_interaction("json_exchange", message, json.dumps(response))

        return response

    async def _log_interaction(self, interaction_type: str, sent: str, received: str):
        """Log an interaction to the database."""
        try:
            pool = await db.get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO agent_interactions (book_id, agent_name, role, interaction_type, content, context)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    self.book_id,
                    self.agent_key,
                    self.role,
                    interaction_type,
                    received,
                    json.dumps({"sent": sent[:500]}),  # Truncate sent for storage
                )
        except Exception as e:
            logger.warning(f"Failed to log interaction: {e}")

    def reset_conversation(self):
        """Clear conversation history (free up memory)."""
        self.conversation = []
