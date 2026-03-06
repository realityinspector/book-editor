"""Browser UI for viewing book editor outputs. Ulysses-inspired reading experience."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from book_editor import db
from book_editor.config import settings

router = APIRouter()
_tpl = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _auth_ok(req: Request) -> bool:
    if not settings.access_key:
        return True
    token = req.cookies.get("access_token", "")
    return token == hashlib.sha256(settings.access_key.encode()).hexdigest()


def _fmt(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%b %d, %Y at %I:%M %p")
    return str(dt) if dt else ""


def _fmt_short(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%b %d, %Y")
    return str(dt) if dt else ""


def _model_short(model: str) -> str:
    """Shorten model name for pill display."""
    # "google/gemini-2.5-pro" -> "gemini-2.5-pro"
    return model.split("/")[-1] if "/" in model else model


# ── Auth ──


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if _auth_ok(request):
        return RedirectResponse("/browse", status_code=302)
    return _tpl.TemplateResponse("access.html", {"request": request, "error": None})


@router.post("/auth")
async def auth(request: Request, key: str = Form(...)):
    if not settings.access_key or key == settings.access_key:
        resp = RedirectResponse("/browse", status_code=302)
        resp.set_cookie(
            "access_token",
            hashlib.sha256(key.encode()).hexdigest(),
            httponly=True, max_age=86400 * 30, samesite="lax",
        )
        return resp
    return _tpl.TemplateResponse(
        "access.html", {"request": request, "error": "Invalid key"}, status_code=403
    )


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie("access_token")
    return resp


# ── Library ──


@router.get("/browse", response_class=HTMLResponse)
async def home(request: Request):
    if not _auth_ok(request):
        return RedirectResponse("/", status_code=302)

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        books = await conn.fetch("""
            SELECT b.id, b.title, b.author, b.total_chapters, b.created_at,
                   COUNT(d.id) as draft_count
            FROM books b LEFT JOIN book_drafts d ON d.book_id = b.id
            GROUP BY b.id ORDER BY b.created_at DESC
        """)
        statuses = {}
        for b in books:
            rows = await conn.fetch(
                "SELECT pipeline, stage FROM pipeline_status WHERE book_id = $1", b["id"]
            )
            statuses[b["id"]] = {r["pipeline"]: r["stage"] for r in rows}

    return _tpl.TemplateResponse("home.html", {
        "request": request,
        "books": [dict(b) for b in books],
        "statuses": statuses,
        "fmt": _fmt,
    })


# ── Book Detail ──


@router.get("/browse/book/{book_id}", response_class=HTMLResponse)
async def book_detail(request: Request, book_id: int):
    if not _auth_ok(request):
        return RedirectResponse("/", status_code=302)

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        book = await conn.fetchrow("SELECT * FROM books WHERE id = $1", book_id)
        if not book:
            return HTMLResponse("<h1>Not found</h1>", status_code=404)

        chapters = await conn.fetch("""
            SELECT id, original_index, title, word_count, is_epilogue
            FROM chapters WHERE book_id = $1 ORDER BY original_index
        """, book_id)

        drafts_raw = await conn.fetch("""
            SELECT id, version, assembly_notes, created_at, LENGTH(full_text) as char_count
            FROM book_drafts WHERE book_id = $1 ORDER BY created_at DESC
        """, book_id)

        fb_summary = {}
        for d in drafts_raw:
            fb = await conn.fetch("""
                SELECT reviewer_name, overall_score, round
                FROM audience_feedback WHERE draft_id = $1 ORDER BY round, reviewer_name
            """, d["id"])
            fb_summary[d["id"]] = [dict(f) for f in fb]

        pipelines = await conn.fetch("""
            SELECT pipeline, stage, progress, detail, error, updated_at
            FROM pipeline_status WHERE book_id = $1 ORDER BY started_at
        """, book_id)

        interaction_count = await conn.fetchval(
            "SELECT COUNT(*) FROM agent_interactions WHERE book_id = $1", book_id
        )

    # Process drafts and split into groups
    all_drafts = []
    for d in drafts_raw:
        dd = dict(d)
        dd["approx_words"] = (dd["char_count"] or 0) // 6
        fb = fb_summary.get(d["id"], [])
        scores = [f["overall_score"] for f in fb if f.get("overall_score") is not None]
        dd["review_count"] = len(fb)
        dd["avg_score"] = round(sum(scores) / len(scores), 1) if scores else None
        all_drafts.append(dd)

    # Split: micro books vs variants, take latest of each version
    micro_drafts = [d for d in all_drafts if d["version"] == 0]
    variant_drafts = [d for d in all_drafts if d["version"] > 0]

    # Deduplicate: keep only the latest draft per version number
    seen_versions = set()
    latest_variants = []
    previous_variants = []
    for d in variant_drafts:  # already sorted by created_at DESC
        if d["version"] not in seen_versions:
            seen_versions.add(d["version"])
            latest_variants.append(d)
        else:
            previous_variants.append(d)

    # Sort latest variants by version ascending for display
    latest_variants.sort(key=lambda d: d["version"])

    total_words = sum(ch["word_count"] for ch in chapters)

    # Pipeline status summary
    pipeline_stage = "idle"
    for p in pipelines:
        if "FAIL" in (p.get("stage") or ""):
            pipeline_stage = "failed"
            break
        if p.get("stage") == "complete":
            pipeline_stage = "complete"
        elif p.get("stage") not in ("complete", "queued", None):
            pipeline_stage = "running"

    # Model info
    models = {
        "editor": _model_short(settings.editor_model),
        "stylist": _model_short(settings.stylist_model),
        "worker": _model_short(settings.worker_model),
        "audience": _model_short(settings.audience_model),
        "judge": _model_short(settings.judge_model),
        "micro": _model_short(settings.micro_model),
    }

    return _tpl.TemplateResponse("book.html", {
        "request": request,
        "book": dict(book),
        "chapters": [dict(c) for c in chapters],
        "latest_variants": latest_variants,
        "micro_drafts": micro_drafts,
        "previous_variants": previous_variants,
        "pipelines": [dict(p) for p in pipelines],
        "pipeline_stage": pipeline_stage,
        "interaction_count": interaction_count,
        "total_words": total_words,
        "models": models,
        "fmt": _fmt,
        "fmt_short": _fmt_short,
    })


# ── Draft Reader ──


@router.get("/browse/draft/{draft_id}", response_class=HTMLResponse)
async def draft_reader(request: Request, draft_id: int):
    if not _auth_ok(request):
        return RedirectResponse("/", status_code=302)

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        draft = await conn.fetchrow("SELECT * FROM book_drafts WHERE id = $1", draft_id)
        if not draft:
            return HTMLResponse("<h1>Not found</h1>", status_code=404)

        book = await conn.fetchrow(
            "SELECT id, title, author FROM books WHERE id = $1", draft["book_id"]
        )

        feedback_raw = await conn.fetch("""
            SELECT reviewer_name, reviewer_persona, round,
                   positive_feedback, critical_feedback, overall_score
            FROM audience_feedback WHERE draft_id = $1 ORDER BY round, reviewer_name
        """, draft_id)

        # Get sibling drafts for navigation (same run = latest per version)
        siblings_raw = await conn.fetch("""
            SELECT id, version FROM book_drafts
            WHERE book_id = $1 AND version > 0
            ORDER BY created_at DESC
        """, draft["book_id"])

    # Deduplicate siblings to latest per version
    seen = set()
    siblings = []
    for s in siblings_raw:
        if s["version"] not in seen:
            seen.add(s["version"])
            siblings.append(dict(s))
    siblings.sort(key=lambda s: s["version"])

    # Parse feedback JSON
    feedback = []
    for f in feedback_raw:
        fd = dict(f)
        for key in ("positive_feedback", "critical_feedback"):
            try:
                fd[key] = json.loads(fd[key]) if fd[key] else []
            except (json.JSONDecodeError, TypeError):
                fd[key] = [fd[key]] if fd[key] else []
        feedback.append(fd)

    # Group feedback by round
    feedback_by_round = {}
    for f in feedback:
        r = f.get("round", 1)
        feedback_by_round.setdefault(r, []).append(f)

    text = draft["full_text"] or ""
    draft_dict = dict(draft)

    # Model info based on draft type
    if draft_dict["version"] == 0:
        model_used = _model_short(settings.micro_model)
    else:
        model_used = _model_short(settings.editor_model)

    # Compute avg score
    all_scores = [f["overall_score"] for f in feedback if f.get("overall_score") is not None]
    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else None

    return _tpl.TemplateResponse("reader.html", {
        "request": request,
        "draft": draft_dict,
        "book": dict(book),
        "feedback": feedback,
        "feedback_by_round": feedback_by_round,
        "siblings": siblings,
        "word_count": len(text.split()),
        "char_count": len(text),
        "model_used": model_used,
        "avg_score": avg_score,
        "fmt": _fmt,
        "fmt_short": _fmt_short,
    })


# ── Interaction Log ──


@router.get("/browse/book/{book_id}/log", response_class=HTMLResponse)
async def interaction_log(request: Request, book_id: int):
    if not _auth_ok(request):
        return RedirectResponse("/", status_code=302)

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        book = await conn.fetchrow(
            "SELECT id, title, author FROM books WHERE id = $1", book_id
        )
        if not book:
            return HTMLResponse("<h1>Not found</h1>", status_code=404)

        interactions = await conn.fetch("""
            SELECT agent_name, role, interaction_type,
                   LEFT(content, 3000) as content,
                   LENGTH(content) as full_length,
                   created_at
            FROM agent_interactions WHERE book_id = $1 ORDER BY created_at ASC
        """, book_id)

    return _tpl.TemplateResponse("log.html", {
        "request": request,
        "book": dict(book),
        "interactions": [dict(i) for i in interactions],
        "fmt": _fmt,
    })


# ── Annotations API ──


@router.get("/api/drafts/{draft_id}/annotations")
async def get_annotations(request: Request, draft_id: int):
    if not _auth_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, draft_id, author_name, selected_text, prefix_context,
                      suffix_context, comment, rating, good_for_normies,
                      bad_for_normies, created_at
               FROM annotations WHERE draft_id = $1 ORDER BY created_at""",
            draft_id,
        )
    return JSONResponse([
        {**dict(r), "created_at": r["created_at"].isoformat()} for r in rows
    ])


@router.post("/api/drafts/{draft_id}/annotations")
async def create_annotation(request: Request, draft_id: int):
    if not _auth_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    rating = int(body.get("rating", 0))
    if rating < -2 or rating > 3:
        return JSONResponse({"error": "rating must be between -2 and 3"}, status_code=400)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO annotations
               (draft_id, author_name, selected_text, prefix_context, suffix_context,
                comment, rating, good_for_normies, bad_for_normies)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               RETURNING id, created_at""",
            draft_id,
            body.get("author_name", ""),
            body.get("selected_text", ""),
            body.get("prefix_context", ""),
            body.get("suffix_context", ""),
            body.get("comment", ""),
            rating,
            bool(body.get("good_for_normies", False)),
            bool(body.get("bad_for_normies", False)),
        )
    return JSONResponse({"id": row["id"], "created_at": row["created_at"].isoformat()})


@router.delete("/api/annotations/{annotation_id}")
async def delete_annotation(request: Request, annotation_id: int):
    if not _auth_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM annotations WHERE id = $1", annotation_id)
    return JSONResponse({"ok": True})
