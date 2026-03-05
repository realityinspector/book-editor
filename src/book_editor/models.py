from pydantic import BaseModel
from datetime import datetime


class Book(BaseModel):
    id: int
    title: str
    author: str | None = None
    source_filename: str | None = None
    total_chapters: int = 0
    created_at: datetime | None = None


class Chapter(BaseModel):
    id: int
    book_id: int
    original_index: int
    title: str | None = None
    content: str
    word_count: int = 0
    is_epilogue: bool = False
    has_attributed_quotes: bool = False
    metadata: dict = {}


class ChapterRevision(BaseModel):
    id: int
    chapter_id: int
    version: int
    content: str
    agent_name: str
    revision_notes: str | None = None
    status: str = "draft"


class BookDraft(BaseModel):
    id: int
    book_id: int
    version: int
    chapter_order: list[dict]
    first_chapter_content: str | None = None
    last_chapter_content: str | None = None
    assembly_notes: str | None = None
    full_text: str | None = None


class AudienceFeedbackItem(BaseModel):
    id: int
    draft_id: int
    reviewer_name: str
    reviewer_persona: str | None = None
    round: int
    positive_feedback: str | None = None
    critical_feedback: str | None = None
    overall_score: int | None = None


class AgentMessage(BaseModel):
    role: str  # system, user, assistant
    content: str


class PipelineStatus(BaseModel):
    book_id: int
    stage: str
    progress: float  # 0.0 - 1.0
    detail: str = ""
    error: str | None = None
