"""Test configuration — set required env vars before any imports."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/book_editor_test")
os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-test-key")
