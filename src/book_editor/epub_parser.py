"""Parse .epub files into chapters as markdown, store in database."""

import logging
import re

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from markdownify import markdownify as md

from book_editor import db

logger = logging.getLogger(__name__)


def epub_to_chapters(epub_path: str) -> list[dict]:
    """
    Parse an .epub file into a list of chapter dicts.
    Each dict: {index, title, content_md, word_count, has_attributed_quotes}
    """
    book = epub.read_epub(epub_path, options={"ignore_ncx": True})

    # Extract metadata
    title = book.get_metadata("DC", "title")
    title = title[0][0] if title else "Untitled"
    author = book.get_metadata("DC", "creator")
    author = author[0][0] if author else "Unknown"

    chapters = []
    idx = 0

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        html_content = item.get_content().decode("utf-8", errors="replace")
        soup = BeautifulSoup(html_content, "html.parser")

        # Skip near-empty items (cover pages, TOC stubs, etc.)
        text = soup.get_text(strip=True)
        if len(text) < 50:
            continue

        # Extract title from first heading if present
        heading = soup.find(re.compile(r"^h[1-3]$"))
        ch_title = heading.get_text(strip=True) if heading else f"Chapter {idx + 1}"

        # Convert to markdown
        content_md = md(html_content, heading_style="ATX", strip=["img", "script", "style"])
        content_md = _clean_markdown(content_md)

        word_count = len(content_md.split())

        # Detect attributed quotes (lines starting with > followed by attribution)
        has_quotes = bool(re.search(
            r'["\u201c].{20,}["\u201d]\s*[-\u2014]\s*\w',
            content_md
        ))

        chapters.append({
            "index": idx,
            "title": ch_title,
            "content_md": content_md,
            "word_count": word_count,
            "has_attributed_quotes": has_quotes,
        })
        idx += 1

    logger.info(f"Parsed '{title}' by {author}: {len(chapters)} chapters, {sum(c['word_count'] for c in chapters)} words")
    return {"title": title, "author": author, "chapters": chapters}


def _clean_markdown(text: str) -> str:
    """Clean up markdown artifacts from conversion."""
    # Collapse excessive blank lines
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    # Remove leftover HTML entities
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    # Strip leading/trailing whitespace per line while preserving structure
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


async def ingest_epub(epub_path: str) -> int:
    """Parse an epub and store all chapters in the database. Returns book_id."""
    parsed = epub_to_chapters(epub_path)
    pool = await db.get_pool()

    async with pool.acquire() as conn:
        book_id = await conn.fetchval(
            """INSERT INTO books (title, author, source_filename, total_chapters)
               VALUES ($1, $2, $3, $4) RETURNING id""",
            parsed["title"],
            parsed["author"],
            epub_path.split("/")[-1],
            len(parsed["chapters"]),
        )

        for ch in parsed["chapters"]:
            await conn.execute(
                """INSERT INTO chapters (book_id, original_index, title, content, word_count, has_attributed_quotes)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                book_id,
                ch["index"],
                ch["title"],
                ch["content_md"],
                ch["word_count"],
                ch["has_attributed_quotes"],
            )

    logger.info(f"Ingested book_id={book_id}: '{parsed['title']}' with {len(parsed['chapters'])} chapters")
    return book_id
