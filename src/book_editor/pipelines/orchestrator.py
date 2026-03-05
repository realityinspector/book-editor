"""Main orchestrator: coordinates the micro-book dry run and full pipeline."""

import logging

from book_editor.epub_parser import ingest_epub
from book_editor.pipelines.micro_book import run_micro_book_pipeline
from book_editor.pipelines.full_book import run_full_book_pipeline

logger = logging.getLogger(__name__)


async def run_pipeline(epub_path: str, skip_micro: bool = False) -> dict:
    """
    Full orchestration:
    1. Ingest EPUB → chapters in DB
    2. Run micro-book dry run (free model, proves pipeline works)
    3. Run full book editing pipeline
    """
    results = {}

    # Step 1: Ingest
    logger.info(f"Ingesting EPUB: {epub_path}")
    book_id = await ingest_epub(epub_path)
    results["book_id"] = book_id

    # Step 2: Micro-book dry run
    if not skip_micro:
        logger.info("Running micro-book dry run...")
        micro_result = await run_micro_book_pipeline(book_id)
        results["micro_book"] = micro_result

        if micro_result.get("status") != "complete":
            logger.error("Micro-book dry run failed — aborting full pipeline")
            results["status"] = "micro_book_failed"
            return results

        logger.info(f"Micro-book dry run successful: {micro_result.get('word_count', 0)} words")

    # Step 3: Full pipeline
    logger.info("Running full book editing pipeline...")
    full_result = await run_full_book_pipeline(book_id)
    results["full_pipeline"] = full_result
    results["status"] = full_result.get("status", "unknown")

    return results
