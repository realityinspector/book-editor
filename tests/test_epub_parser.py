"""Tests for epub parsing (unit tests that don't need DB or API)."""

import pytest
from book_editor.epub_parser import _clean_markdown


def test_clean_markdown_collapses_blank_lines():
    text = "Hello\n\n\n\n\n\nWorld"
    result = _clean_markdown(text)
    assert "\n\n\n\n" not in result
    assert "Hello" in result
    assert "World" in result


def test_clean_markdown_replaces_entities():
    text = "Hello&nbsp;World &amp; Friends"
    result = _clean_markdown(text)
    assert "&nbsp;" not in result
    assert "&amp;" not in result
    assert "Hello World" in result
    assert "& Friends" in result


def test_clean_markdown_strips_trailing_whitespace():
    text = "Hello   \nWorld   "
    result = _clean_markdown(text)
    lines = result.split("\n")
    for line in lines:
        assert line == line.rstrip()
