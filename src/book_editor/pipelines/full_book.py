"""Full book editing pipeline: the complete multi-agent workflow."""

import asyncio
import json
import logging

from book_editor import db
from book_editor.config import settings
from book_editor.agents.editor import EditorAgent
from book_editor.agents.stylist import StylistAgent
from book_editor.agents.judge import JudgeAgent
from book_editor.agents.chapter_worker import ChapterWorkerAgent
from book_editor.agents.audience import create_audience_panel

logger = logging.getLogger(__name__)

# Pipeline status tracking (in-memory, could move to DB for persistence)
_pipeline_status: dict[int, dict] = {}


def get_status(book_id: int) -> dict:
    return _pipeline_status.get(book_id, {"stage": "idle", "progress": 0.0})


def _update_status(book_id: int, stage: str, progress: float, detail: str = ""):
    _pipeline_status[book_id] = {"stage": stage, "progress": progress, "detail": detail}
    logger.info(f"Pipeline [{book_id}] {stage}: {progress:.0%} - {detail}")


async def run_full_book_pipeline(book_id: int) -> dict:
    """
    Execute the complete book editing pipeline:

    Stage 1: Editor reads entire book, develops vision
    Stage 2: Stylist analyzes voice, creates style brief
    Stage 3: Editor + Stylist debate (3 rounds)
    Stage 4: Editor generates chapter-level instructions
    Stage 5: Worker swarm edits chapters (with Judge validation)
    Stage 6: Editor determines final chapter order (3 variants)
    Stage 7: Editor writes variant first chapters
    Stage 8: Assemble 3 draft versions
    Stage 9: Audience panel reviews each version (3 rounds)
    Stage 10: Final report
    """
    results = {"book_id": book_id, "stages": {}}

    try:
        # ── Stage 1: Editor reads the entire book ──
        _update_status(book_id, "editor_reading", 0.05, "Editor reading entire book")
        editor = EditorAgent(model=settings.editor_model, book_id=book_id)
        assessment = await editor.read_entire_book()
        results["stages"]["editor_assessment"] = assessment

        # ── Stage 2: Stylist analyzes voice ──
        _update_status(book_id, "stylist_analysis", 0.10, "Stylist analyzing writing voice")
        stylist = StylistAgent(model=settings.stylist_model, book_id=book_id)

        pool = await db.get_pool()
        async with pool.acquire() as conn:
            sample_chapters = await conn.fetch(
                """SELECT id, title, content FROM chapters
                   WHERE book_id = $1 ORDER BY original_index LIMIT 5""",
                book_id,
            )

        voice_analysis = await stylist.analyze_voice(
            [dict(ch) for ch in sample_chapters]
        )
        results["stages"]["voice_analysis"] = voice_analysis

        # ── Stage 3: Editor + Stylist debate (3 rounds) ──
        _update_status(book_id, "debate", 0.15, "Editor and Stylist debating book direction")
        debate_log = []

        stylist_position = await stylist.debate_with_editor(json.dumps(assessment))
        debate_log.append({"round": 1, "stylist": stylist_position})

        editor_response = await editor.debate_with_stylist(stylist_position)
        debate_log.append({"round": 1, "editor": editor_response})

        for round_num in range(2, 4):
            _update_status(book_id, "debate", 0.15 + round_num * 0.02, f"Debate round {round_num}")
            stylist_reply = await stylist.debate_with_editor(editor_response)
            debate_log.append({"round": round_num, "stylist": stylist_reply})

            editor_response = await editor.debate_with_stylist(stylist_reply)
            debate_log.append({"round": round_num, "editor": editor_response})

        results["stages"]["debate"] = debate_log

        # ── Stage 3.5: Stylist produces style brief ──
        style_brief = await stylist.provide_style_brief()
        results["stages"]["style_brief"] = style_brief

        # ── Stage 4: Editor generates chapter instructions ──
        _update_status(book_id, "chapter_instructions", 0.25, "Editor generating chapter instructions")
        async with pool.acquire() as conn:
            all_chapters = await conn.fetch(
                """SELECT id, original_index, title, content, is_epilogue, has_attributed_quotes
                   FROM chapters WHERE book_id = $1 ORDER BY original_index""",
                book_id,
            )

        chapter_instructions = {}
        for ch in all_chapters:
            if ch["is_epilogue"]:
                chapter_instructions[ch["id"]] = "EPILOGUE — DO NOT MODIFY"
                continue
            instructions = await editor.generate_chapter_instructions(
                ch["id"], ch["content"], ch["title"]
            )
            chapter_instructions[ch["id"]] = instructions

        results["stages"]["chapter_instructions_count"] = len(chapter_instructions)

        # ── Stage 5: Worker swarm edits chapters with Judge loop ──
        _update_status(book_id, "chapter_editing", 0.30, "Worker swarm editing chapters")
        judge = JudgeAgent(model=settings.judge_model, book_id=book_id)
        await judge.load_memory()

        semaphore = asyncio.Semaphore(settings.max_concurrent_workers)
        edit_results = {}

        async def edit_chapter_with_judge(ch_record):
            """Edit a single chapter, get judge approval, retry if rejected."""
            ch_id = ch_record["id"]
            async with semaphore:
                worker = ChapterWorkerAgent(
                    model=settings.worker_model,
                    book_id=book_id,
                    worker_id=ch_id,
                )

                max_attempts = 3
                for attempt in range(1, max_attempts + 1):
                    _update_status(
                        book_id, "chapter_editing",
                        0.30 + 0.35 * (len(edit_results) / max(len(all_chapters), 1)),
                        f"Editing chapter {ch_record['original_index'] + 1} (attempt {attempt})",
                    )

                    revision = await worker.revise_chapter(
                        chapter_id=ch_id,
                        original_content=ch_record["content"],
                        editor_instructions=chapter_instructions.get(ch_id, "General revision"),
                        style_brief=style_brief,
                        is_epilogue=ch_record["is_epilogue"],
                    )

                    if ch_record["is_epilogue"]:
                        edit_results[ch_id] = {"status": "preserved", "attempts": 1}
                        return

                    # Judge review
                    judgment = await judge.judge_revision(
                        chapter_id=ch_id,
                        original_content=ch_record["content"],
                        revised_content=revision["content"],
                        revision_notes=revision["revision_notes"],
                        style_brief=style_brief,
                        editor_instructions=chapter_instructions.get(ch_id, ""),
                    )

                    if judgment.get("decision") == "approved":
                        # Update revision status
                        async with pool.acquire() as conn:
                            await conn.execute(
                                """UPDATE chapter_revisions SET status = 'approved'
                                   WHERE chapter_id = $1 AND version = $2""",
                                ch_id,
                                revision["version"],
                            )
                        edit_results[ch_id] = {"status": "approved", "attempts": attempt}
                        return
                    else:
                        logger.info(
                            f"Chapter {ch_id} rejected (attempt {attempt}): "
                            f"{judgment.get('issues', ['unknown'])}"
                        )
                        # Feed judge feedback back into worker for next attempt
                        worker.conversation.append({
                            "role": "user",
                            "content": f"Your revision was REJECTED. Issues: {judgment.get('issues', [])}. "
                                       f"Guidance: {judgment.get('guidance', 'Try again.')}",
                        })

                # Max attempts reached — approve the last one with a note
                edit_results[ch_id] = {"status": "approved_after_max_attempts", "attempts": max_attempts}
                async with pool.acquire() as conn:
                    await conn.execute(
                        """UPDATE chapter_revisions SET status = 'approved'
                           WHERE chapter_id = $1
                           AND version = (SELECT MAX(version) FROM chapter_revisions WHERE chapter_id = $1)""",
                        ch_id,
                    )

        # Run workers concurrently
        tasks = [edit_chapter_with_judge(ch) for ch in all_chapters]
        await asyncio.gather(*tasks)
        results["stages"]["chapter_edits"] = edit_results

        # ── Stage 6: Editor determines chapter order (3 variants) ──
        _update_status(book_id, "ordering", 0.70, "Editor determining chapter order")

        base_order = await editor.determine_chapter_order()
        results["stages"]["base_chapter_order"] = base_order

        # Create 3 variants by rotating the chapter order
        # Variant 1: editor's primary order
        # Variant 2: different first chapter (second choice leads)
        # Variant 3: different first chapter (third choice leads)
        variants = []
        base_chapters = base_order.get("chapter_order", [])
        included = [c for c in base_chapters if c.get("include", True)]

        for v in range(3):
            variant = included.copy()
            if v > 0 and len(variant) > v:
                # Rotate: move the v-th chapter to position 0
                moved = variant.pop(v)
                variant.insert(0, moved)
            # Re-number positions
            for i, ch in enumerate(variant):
                ch["position"] = i
            variants.append(variant)

        results["stages"]["variants"] = [{"variant": i + 1, "order": v} for i, v in enumerate(variants)]

        # ── Stage 7: Editor writes variant first chapters ──
        _update_status(book_id, "first_chapters", 0.75, "Editor writing variant first chapters")
        variant_first_chapters = []
        for v_num, variant in enumerate(variants, 1):
            first_ch = await editor.write_variant_first_chapter(v_num, variant)
            variant_first_chapters.append(first_ch)

        # ── Stage 8: Assemble 3 draft versions ──
        _update_status(book_id, "assembly", 0.80, "Assembling draft versions")
        draft_ids = []
        for v_num, (variant, first_ch) in enumerate(zip(variants, variant_first_chapters), 1):
            draft_id = await editor.assemble_draft(v_num, variant)
            # Prepend the variant first chapter
            async with pool.acquire() as conn:
                await conn.execute(
                    """UPDATE book_drafts SET first_chapter_content = $1 WHERE id = $2""",
                    first_ch,
                    draft_id,
                )
                # Update full_text to include the first chapter
                draft = await conn.fetchrow("SELECT full_text FROM book_drafts WHERE id = $1", draft_id)
                if draft:
                    updated_text = f"# Opening\n\n{first_ch}\n\n---\n\n{draft['full_text']}"
                    await conn.execute(
                        "UPDATE book_drafts SET full_text = $1 WHERE id = $2",
                        updated_text,
                        draft_id,
                    )
            draft_ids.append(draft_id)

        results["stages"]["draft_ids"] = draft_ids

        # ── Stage 9: Audience panel reviews (3 rounds per draft) ──
        _update_status(book_id, "audience_review", 0.85, "Audience panel reviewing drafts")
        audience_panel = create_audience_panel(model=settings.audience_model, book_id=book_id)
        all_feedback = {}

        for draft_id in draft_ids:
            all_feedback[draft_id] = []
            for round_num in range(1, 4):
                _update_status(
                    book_id, "audience_review",
                    0.85 + 0.12 * (draft_ids.index(draft_id) * 3 + round_num) / (len(draft_ids) * 3),
                    f"Draft {draft_id} round {round_num}",
                )
                round_feedback = []
                # Run all 3 reviewers in parallel for this round
                review_tasks = [
                    reviewer.review_draft(draft_id, round_num)
                    for reviewer in audience_panel
                ]
                reviews = await asyncio.gather(*review_tasks)
                for reviewer, review in zip(audience_panel, reviews):
                    round_feedback.append({
                        "reviewer": reviewer.persona_name,
                        "round": round_num,
                        "feedback": review,
                    })
                all_feedback[draft_id].append(round_feedback)

        results["stages"]["audience_feedback"] = all_feedback

        # ── Stage 10: Final report ──
        _update_status(book_id, "complete", 1.0, "Pipeline complete")

        # Compute summary scores
        for draft_id, rounds in all_feedback.items():
            scores = []
            for round_reviews in rounds:
                for r in round_reviews:
                    score = r["feedback"].get("overall_score")
                    if score:
                        scores.append(score)
            if scores:
                results.setdefault("summary", {})[f"draft_{draft_id}_avg_score"] = sum(scores) / len(scores)

        results["status"] = "complete"
        return results

    except Exception as e:
        logger.exception(f"Pipeline failed for book {book_id}")
        _update_status(book_id, "error", 0.0, str(e))
        results["status"] = "error"
        results["error"] = str(e)
        return results
