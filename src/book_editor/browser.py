"""Browser UI for viewing book editor outputs. User auth, sharing, and mentions."""

import hashlib
import hmac
import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from book_editor import db
from book_editor.config import settings

router = APIRouter()
_tpl = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ── Password hashing ──

def _hash_password(password: str) -> str:
    salt = hashlib.sha256(settings.session_secret.encode()).hexdigest()[:16]
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100_000
    ).hex()


def _verify_password(password: str, password_hash: str) -> bool:
    return _hash_password(password) == password_hash


# ── Session management ──

def _make_session_token(user_id: int) -> str:
    sig = hmac.new(
        settings.session_secret.encode(), str(user_id).encode(), hashlib.sha256
    ).hexdigest()
    return f"{user_id}.{sig}"


def _verify_session_token(token: str) -> int | None:
    if not token or "." not in token:
        return None
    parts = token.split(".", 1)
    try:
        user_id = int(parts[0])
    except ValueError:
        return None
    expected = hmac.new(
        settings.session_secret.encode(), str(user_id).encode(), hashlib.sha256
    ).hexdigest()
    if hmac.compare_digest(parts[1], expected):
        return user_id
    return None


async def _get_current_user(req: Request) -> dict | None:
    """Get the current logged-in user from session cookie, or None."""
    token = req.cookies.get("session_token", "")
    user_id = _verify_session_token(token)
    if not user_id:
        # Backward compat: check old access_key cookie
        if settings.access_key:
            old_token = req.cookies.get("access_token", "")
            if old_token == hashlib.sha256(settings.access_key.encode()).hexdigest():
                return {"id": 0, "username": "legacy", "display_name": "Legacy User", "is_admin": True}
        return None
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, display_name, is_admin FROM users WHERE id = $1", user_id
        )
    return dict(row) if row else None


def _fmt(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%b %d, %Y at %I:%M %p")
    return str(dt) if dt else ""


def _fmt_short(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%b %d, %Y")
    return str(dt) if dt else ""


def _model_short(model: str) -> str:
    return model.split("/")[-1] if "/" in model else model


# ── Mention parsing ──

def _parse_mentions(text: str) -> list[str]:
    """Extract @username mentions from text."""
    return re.findall(r'@(\w+)', text or "")


# ── Auth Routes ──


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = await _get_current_user(request)
    if user:
        return RedirectResponse("/browse", status_code=302)
    return _tpl.TemplateResponse("access.html", {"request": request, "error": None, "mode": "login"})


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    user = await _get_current_user(request)
    if user:
        return RedirectResponse("/browse", status_code=302)
    return _tpl.TemplateResponse("access.html", {"request": request, "error": None, "mode": "register"})


@router.post("/auth")
async def auth(request: Request, username: str = Form(...), password: str = Form(...)):
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, password_hash FROM users WHERE username = $1", username.lower().strip()
        )
    if not row or not _verify_password(password, row["password_hash"]):
        return _tpl.TemplateResponse(
            "access.html", {"request": request, "error": "Invalid username or password", "mode": "login"},
            status_code=403,
        )
    resp = RedirectResponse("/browse", status_code=302)
    resp.set_cookie(
        "session_token", _make_session_token(row["id"]),
        httponly=True, max_age=86400 * 30, samesite="lax",
    )
    return resp


@router.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(""),
):
    username = username.lower().strip()
    if not username or len(username) < 2:
        return _tpl.TemplateResponse(
            "access.html", {"request": request, "error": "Username must be at least 2 characters", "mode": "register"},
            status_code=400,
        )
    if not password or len(password) < 4:
        return _tpl.TemplateResponse(
            "access.html", {"request": request, "error": "Password must be at least 4 characters", "mode": "register"},
            status_code=400,
        )
    if not re.match(r'^[a-z0-9_]+$', username):
        return _tpl.TemplateResponse(
            "access.html", {"request": request, "error": "Username: lowercase letters, numbers, underscores only", "mode": "register"},
            status_code=400,
        )

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        # First user becomes admin
        user_count = await conn.fetchval("SELECT COUNT(*) FROM users")
        is_admin = user_count == 0

        existing = await conn.fetchval("SELECT 1 FROM users WHERE username = $1", username)
        if existing:
            return _tpl.TemplateResponse(
                "access.html", {"request": request, "error": "Username already taken", "mode": "register"},
                status_code=400,
            )

        row = await conn.fetchrow(
            """INSERT INTO users (username, password_hash, display_name, is_admin)
               VALUES ($1, $2, $3, $4) RETURNING id""",
            username,
            _hash_password(password),
            display_name.strip() or username,
            is_admin,
        )

    resp = RedirectResponse("/browse", status_code=302)
    resp.set_cookie(
        "session_token", _make_session_token(row["id"]),
        httponly=True, max_age=86400 * 30, samesite="lax",
    )
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie("session_token")
    resp.delete_cookie("access_token")
    return resp


# ── Library ──


@router.get("/browse", response_class=HTMLResponse)
async def home(request: Request):
    user = await _get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        # Get books user owns or has access to (admin sees all)
        if user.get("is_admin") or user["id"] == 0:
            books = await conn.fetch("""
                SELECT b.id, b.title, b.author, b.total_chapters, b.created_at,
                       b.owner_id, COUNT(d.id) as draft_count
                FROM books b LEFT JOIN book_drafts d ON d.book_id = b.id
                GROUP BY b.id ORDER BY b.created_at DESC
            """)
        else:
            books = await conn.fetch("""
                SELECT b.id, b.title, b.author, b.total_chapters, b.created_at,
                       b.owner_id, COUNT(d.id) as draft_count
                FROM books b LEFT JOIN book_drafts d ON d.book_id = b.id
                WHERE b.owner_id = $1 OR b.owner_id IS NULL
                      OR b.id IN (SELECT book_id FROM book_shares WHERE shared_with_id = $1)
                GROUP BY b.id ORDER BY b.created_at DESC
            """, user["id"])

        statuses = {}
        for b in books:
            rows = await conn.fetch(
                "SELECT pipeline, stage FROM pipeline_status WHERE book_id = $1", b["id"]
            )
            statuses[b["id"]] = {r["pipeline"]: r["stage"] for r in rows}

        # Get owner display names
        owner_names = {}
        for b in books:
            if b["owner_id"]:
                if b["owner_id"] not in owner_names:
                    orow = await conn.fetchrow(
                        "SELECT display_name, username FROM users WHERE id = $1", b["owner_id"]
                    )
                    owner_names[b["owner_id"]] = orow["display_name"] or orow["username"] if orow else "?"

        # Unread mention count
        mention_count = 0
        if user["id"] > 0:
            mention_count = await conn.fetchval(
                "SELECT COUNT(*) FROM mentions WHERE mentioned_user_id = $1 AND seen = FALSE",
                user["id"],
            ) or 0

    return _tpl.TemplateResponse("home.html", {
        "request": request,
        "user": user,
        "books": [dict(b) for b in books],
        "statuses": statuses,
        "owner_names": owner_names,
        "mention_count": mention_count,
        "fmt": _fmt,
    })


# ── Book Detail ──


async def _user_can_access_book(conn, user: dict, book_id: int) -> bool:
    if user.get("is_admin") or user["id"] == 0:
        return True
    book = await conn.fetchrow("SELECT owner_id FROM books WHERE id = $1", book_id)
    if not book:
        return False
    if book["owner_id"] is None or book["owner_id"] == user["id"]:
        return True
    shared = await conn.fetchval(
        "SELECT 1 FROM book_shares WHERE book_id = $1 AND shared_with_id = $2",
        book_id, user["id"],
    )
    return bool(shared)


async def _user_can_write_book(conn, user: dict, book_id: int) -> bool:
    if user.get("is_admin") or user["id"] == 0:
        return True
    book = await conn.fetchrow("SELECT owner_id FROM books WHERE id = $1", book_id)
    if not book:
        return False
    if book["owner_id"] is None or book["owner_id"] == user["id"]:
        return True
    shared = await conn.fetchval(
        "SELECT permission FROM book_shares WHERE book_id = $1 AND shared_with_id = $2",
        book_id, user["id"],
    )
    return shared == "write"


@router.get("/browse/book/{book_id}", response_class=HTMLResponse)
async def book_detail(request: Request, book_id: int):
    user = await _get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        if not await _user_can_access_book(conn, user, book_id):
            return HTMLResponse("<h1>Not found</h1>", status_code=404)

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

        # Sharing info
        can_write = await _user_can_write_book(conn, user, book_id)
        shares = await conn.fetch("""
            SELECT bs.id, bs.shared_with_id, bs.permission, bs.created_at,
                   u.username, u.display_name
            FROM book_shares bs JOIN users u ON u.id = bs.shared_with_id
            WHERE bs.book_id = $1 ORDER BY bs.created_at
        """, book_id)

        all_users = await conn.fetch(
            "SELECT id, username, display_name FROM users ORDER BY username"
        )

    # Process drafts
    all_drafts = []
    for d in drafts_raw:
        dd = dict(d)
        dd["approx_words"] = (dd["char_count"] or 0) // 6
        fb = fb_summary.get(d["id"], [])
        scores = [f["overall_score"] for f in fb if f.get("overall_score") is not None]
        dd["review_count"] = len(fb)
        dd["avg_score"] = round(sum(scores) / len(scores), 1) if scores else None
        all_drafts.append(dd)

    micro_drafts = [d for d in all_drafts if d["version"] == 0]
    variant_drafts = [d for d in all_drafts if d["version"] > 0]

    seen_versions = set()
    latest_variants = []
    previous_variants = []
    for d in variant_drafts:
        if d["version"] not in seen_versions:
            seen_versions.add(d["version"])
            latest_variants.append(d)
        else:
            previous_variants.append(d)

    latest_variants.sort(key=lambda d: d["version"])
    total_words = sum(ch["word_count"] for ch in chapters)

    pipeline_stage = "idle"
    for p in pipelines:
        if "FAIL" in (p.get("stage") or ""):
            pipeline_stage = "failed"
            break
        if p.get("stage") == "complete":
            pipeline_stage = "complete"
        elif p.get("stage") not in ("complete", "queued", None):
            pipeline_stage = "running"

    models = {
        "editor": _model_short(settings.editor_model),
        "stylist": _model_short(settings.stylist_model),
        "worker": _model_short(settings.worker_model),
        "audience": _model_short(settings.audience_model),
        "judge": _model_short(settings.judge_model),
        "micro": _model_short(settings.micro_model),
    }

    is_owner = (book["owner_id"] is None or book["owner_id"] == user["id"] or user.get("is_admin"))

    return _tpl.TemplateResponse("book.html", {
        "request": request,
        "user": user,
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
        "is_owner": is_owner,
        "can_write": can_write,
        "shares": [dict(s) for s in shares],
        "all_users": [dict(u) for u in all_users],
        "fmt": _fmt,
        "fmt_short": _fmt_short,
    })


# ── Draft Reader ──


@router.get("/browse/draft/{draft_id}", response_class=HTMLResponse)
async def draft_reader(request: Request, draft_id: int):
    user = await _get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        draft = await conn.fetchrow("SELECT * FROM book_drafts WHERE id = $1", draft_id)
        if not draft:
            return HTMLResponse("<h1>Not found</h1>", status_code=404)

        if not await _user_can_access_book(conn, user, draft["book_id"]):
            return HTMLResponse("<h1>Not found</h1>", status_code=404)

        book = await conn.fetchrow(
            "SELECT id, title, author FROM books WHERE id = $1", draft["book_id"]
        )

        feedback_raw = await conn.fetch("""
            SELECT reviewer_name, reviewer_persona, round,
                   positive_feedback, critical_feedback, overall_score
            FROM audience_feedback WHERE draft_id = $1 ORDER BY round, reviewer_name
        """, draft_id)

        siblings_raw = await conn.fetch("""
            SELECT id, version FROM book_drafts
            WHERE book_id = $1 AND version > 0
            ORDER BY created_at DESC
        """, draft["book_id"])

        # Get all users for @mention autocomplete
        all_users = await conn.fetch(
            "SELECT id, username, display_name FROM users ORDER BY username"
        )

    seen = set()
    siblings = []
    for s in siblings_raw:
        if s["version"] not in seen:
            seen.add(s["version"])
            siblings.append(dict(s))
    siblings.sort(key=lambda s: s["version"])

    feedback = []
    for f in feedback_raw:
        fd = dict(f)
        for key in ("positive_feedback", "critical_feedback"):
            try:
                fd[key] = json.loads(fd[key]) if fd[key] else []
            except (json.JSONDecodeError, TypeError):
                fd[key] = [fd[key]] if fd[key] else []
        feedback.append(fd)

    feedback_by_round = {}
    for f in feedback:
        r = f.get("round", 1)
        feedback_by_round.setdefault(r, []).append(f)

    text = draft["full_text"] or ""
    draft_dict = dict(draft)

    if draft_dict["version"] == 0:
        model_used = _model_short(settings.micro_model)
    else:
        model_used = _model_short(settings.editor_model)

    all_scores = [f["overall_score"] for f in feedback if f.get("overall_score") is not None]
    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else None

    return _tpl.TemplateResponse("reader.html", {
        "request": request,
        "user": user,
        "draft": draft_dict,
        "book": dict(book),
        "feedback": feedback,
        "feedback_by_round": feedback_by_round,
        "siblings": siblings,
        "word_count": len(text.split()),
        "char_count": len(text),
        "model_used": model_used,
        "avg_score": avg_score,
        "all_users": [dict(u) for u in all_users],
        "fmt": _fmt,
        "fmt_short": _fmt_short,
    })


# ── Interaction Log ──


@router.get("/browse/book/{book_id}/log", response_class=HTMLResponse)
async def interaction_log(request: Request, book_id: int):
    user = await _get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        if not await _user_can_access_book(conn, user, book_id):
            return HTMLResponse("<h1>Not found</h1>", status_code=404)

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
        "user": user,
        "book": dict(book),
        "interactions": [dict(i) for i in interactions],
        "fmt": _fmt,
    })


# ── Sharing API ──


@router.post("/api/books/{book_id}/share")
async def share_book(request: Request, book_id: int):
    user = await _get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    username = body.get("username", "").lower().strip()
    permission = body.get("permission", "read")
    if permission not in ("read", "write"):
        return JSONResponse({"error": "permission must be 'read' or 'write'"}, status_code=400)

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        # Only owner/admin can share
        book = await conn.fetchrow("SELECT owner_id FROM books WHERE id = $1", book_id)
        if not book:
            return JSONResponse({"error": "book not found"}, status_code=404)
        if book["owner_id"] is not None and book["owner_id"] != user["id"] and not user.get("is_admin"):
            return JSONResponse({"error": "only the owner can share"}, status_code=403)

        target = await conn.fetchrow("SELECT id, username, display_name FROM users WHERE username = $1", username)
        if not target:
            return JSONResponse({"error": f"user '{username}' not found"}, status_code=404)
        if target["id"] == user["id"]:
            return JSONResponse({"error": "can't share with yourself"}, status_code=400)

        await conn.execute("""
            INSERT INTO book_shares (book_id, shared_by_id, shared_with_id, permission)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (book_id, shared_with_id) DO UPDATE SET permission = $4
        """, book_id, user["id"], target["id"], permission)

    return JSONResponse({
        "ok": True,
        "shared_with": {"id": target["id"], "username": target["username"], "display_name": target["display_name"]},
        "permission": permission,
    })


@router.delete("/api/books/{book_id}/share/{share_id}")
async def unshare_book(request: Request, book_id: int, share_id: int):
    user = await _get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        book = await conn.fetchrow("SELECT owner_id FROM books WHERE id = $1", book_id)
        if not book:
            return JSONResponse({"error": "book not found"}, status_code=404)
        if book["owner_id"] is not None and book["owner_id"] != user["id"] and not user.get("is_admin"):
            return JSONResponse({"error": "only the owner can manage shares"}, status_code=403)
        await conn.execute("DELETE FROM book_shares WHERE id = $1 AND book_id = $2", share_id, book_id)
    return JSONResponse({"ok": True})


# ── Mentions API ──


@router.get("/api/mentions")
async def get_mentions(request: Request):
    user = await _get_current_user(request)
    if not user or user["id"] == 0:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT m.id, m.annotation_id, m.seen, m.created_at,
                   a.selected_text, a.comment, a.draft_id,
                   u.username as from_username, u.display_name as from_name
            FROM mentions m
            JOIN annotations a ON a.id = m.annotation_id
            LEFT JOIN users u ON u.id = a.user_id
            WHERE m.mentioned_user_id = $1
            ORDER BY m.created_at DESC LIMIT 50
        """, user["id"])
    return JSONResponse([
        {**dict(r), "created_at": r["created_at"].isoformat()} for r in rows
    ])


@router.post("/api/mentions/mark-seen")
async def mark_mentions_seen(request: Request):
    user = await _get_current_user(request)
    if not user or user["id"] == 0:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE mentions SET seen = TRUE WHERE mentioned_user_id = $1", user["id"]
        )
    return JSONResponse({"ok": True})


# ── Users API (for autocomplete) ──


@router.get("/api/users")
async def list_users(request: Request):
    user = await _get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, username, display_name FROM users ORDER BY username")
    return JSONResponse([dict(r) for r in rows])


# ── Annotations API ──


@router.get("/api/drafts/{draft_id}/annotations")
async def get_annotations(request: Request, draft_id: int):
    user = await _get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT a.id, a.draft_id, a.author_name, a.selected_text, a.prefix_context,
                      a.suffix_context, a.comment, a.rating, a.good_for_normies,
                      a.bad_for_normies, a.created_at, a.user_id,
                      u.username, u.display_name
               FROM annotations a LEFT JOIN users u ON u.id = a.user_id
               WHERE a.draft_id = $1 ORDER BY a.created_at""",
            draft_id,
        )
    return JSONResponse([
        {**dict(r), "created_at": r["created_at"].isoformat()} for r in rows
    ])


@router.post("/api/drafts/{draft_id}/annotations")
async def create_annotation(request: Request, draft_id: int):
    user = await _get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    rating = int(body.get("rating", 0))
    if rating < -2 or rating > 3:
        return JSONResponse({"error": "rating must be between -2 and 3"}, status_code=400)

    comment = body.get("comment", "")
    author_name = body.get("author_name", "") or user.get("display_name") or user.get("username", "")

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        user_id = user["id"] if user["id"] > 0 else None
        row = await conn.fetchrow(
            """INSERT INTO annotations
               (draft_id, author_name, selected_text, prefix_context, suffix_context,
                comment, rating, good_for_normies, bad_for_normies, user_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               RETURNING id, created_at""",
            draft_id,
            author_name,
            body.get("selected_text", ""),
            body.get("prefix_context", ""),
            body.get("suffix_context", ""),
            comment,
            rating,
            bool(body.get("good_for_normies", False)),
            bool(body.get("bad_for_normies", False)),
            user_id,
        )

        # Process @mentions in comment
        if comment and user_id:
            mentioned_usernames = _parse_mentions(comment)
            for uname in set(mentioned_usernames):
                mentioned = await conn.fetchrow(
                    "SELECT id FROM users WHERE username = $1", uname.lower()
                )
                if mentioned and mentioned["id"] != user_id:
                    await conn.execute(
                        "INSERT INTO mentions (annotation_id, mentioned_user_id) VALUES ($1, $2)",
                        row["id"], mentioned["id"],
                    )

    return JSONResponse({"id": row["id"], "created_at": row["created_at"].isoformat()})


@router.delete("/api/annotations/{annotation_id}")
async def delete_annotation(request: Request, annotation_id: int):
    user = await _get_current_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        # Only annotation owner or admin can delete
        ann = await conn.fetchrow("SELECT user_id FROM annotations WHERE id = $1", annotation_id)
        if ann and ann["user_id"] and user["id"] > 0 and ann["user_id"] != user["id"] and not user.get("is_admin"):
            return JSONResponse({"error": "can only delete your own annotations"}, status_code=403)
        await conn.execute("DELETE FROM annotations WHERE id = $1", annotation_id)
    return JSONResponse({"ok": True})
