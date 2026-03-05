"""Tests for pipeline structure and config."""

import os
import pytest
from book_editor.config import Settings


def test_settings_requires_both_vars():
    """Settings should require DATABASE_URL and OPENROUTER_API_KEY."""
    # Clear env vars temporarily to test validation
    saved_db = os.environ.pop("DATABASE_URL", None)
    saved_key = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        with pytest.raises(Exception):
            Settings(_env_file=None)
    finally:
        if saved_db:
            os.environ["DATABASE_URL"] = saved_db
        if saved_key:
            os.environ["OPENROUTER_API_KEY"] = saved_key


def test_settings_defaults():
    """Settings should have sensible defaults when both required vars are set."""
    s = Settings(database_url="postgresql://localhost/test", openrouter_api_key="sk-test")
    assert s.editor_model == "google/gemini-2.5-pro"
    assert s.micro_model == "meta-llama/llama-3.3-70b-instruct:free"
    assert s.max_concurrent_workers == 5
    assert s.openrouter_base_url == "https://openrouter.ai/api/v1"


def test_chapter_worker_model_is_fast():
    """Worker model should be a fast model for concurrent chapter editing."""
    s = Settings(database_url="postgresql://localhost/test", openrouter_api_key="sk-test")
    assert "flash" in s.worker_model.lower() or "fast" in s.worker_model.lower()
