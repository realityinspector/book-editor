"""Browser UI for viewing book editor outputs. Ulysses-inspired reading experience."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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
            FROM book_drafts WHERE book_id = $1 ORDER BY version
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

    # Process drafts
    drafts = []
    for d in drafts_raw:
        dd = dict(d)
        dd["approx_words"] = (dd["char_count"] or 0) // 6
        fb = fb_summary.get(d["id"], [])
        scores = [f["overall_score"] for f in fb if f.get("overall_score") is not None]
        dd["review_count"] = len(fb)
        dd["avg_score"] = round(sum(scores) / len(scores), 1) if scores else None
        drafts.append(dd)

    total_words = sum(ch["word_count"] for ch in chapters)

    return _tpl.TemplateResponse("book.html", {
        "request": request,
        "book": dict(book),
        "chapters": [dict(c) for c in chapters],
        "drafts": drafts,
        "pipelines": [dict(p) for p in pipelines],
        "interaction_count": interaction_count,
        "total_words": total_words,
        "fmt": _fmt,
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

    text = draft["full_text"] or ""

    # Format chapter_order for display
    draft_dict = dict(draft)
    co = draft_dict.get("chapter_order")
    if co:
        if isinstance(co, str):
            try:
                co = json.loads(co)
            except json.JSONDecodeError:
                pass
        if isinstance(co, (list, dict)):
            draft_dict["chapter_order_display"] = json.dumps(co, indent=2)
        else:
            draft_dict["chapter_order_display"] = str(co)
    else:
        draft_dict["chapter_order_display"] = None

    return _tpl.TemplateResponse("reader.html", {
        "request": request,
        "draft": draft_dict,
        "book": dict(book),
        "feedback": feedback,
        "word_count": len(text.split()),
        "char_count": len(text),
        "fmt": _fmt,
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
            SELECT agent_name, role, interaction_type, content, created_at
            FROM agent_interactions WHERE book_id = $1 ORDER BY created_at ASC
        """, book_id)

    return _tpl.TemplateResponse("log.html", {
        "request": request,
        "book": dict(book),
        "interactions": [dict(i) for i in interactions],
        "fmt": _fmt,
    })
