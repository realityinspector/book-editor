# CLAUDE.md

## Commit Rules
- Do NOT add "Co-Authored-By" trailers to commits
- Do NOT add AI attribution (e.g., "Generated with Claude") to code or commit messages

## What this is
FastAPI + Postgres app with two faces:
1. **Pipeline API** (`src/book_editor/main.py`) — upload an `.epub`, run a multi-agent
   AI revision pipeline, produce draft variants. See README for the full flow.
2. **Reader & annotation web UI** (`src/book_editor/browser.py` + `templates/`) — a public
   `/browse` interface for reading the resulting drafts and annotating them.

## Auth & access model (important invariants)
Reading is public; writing/admin/internals require auth. When changing access logic,
preserve these:
- **Anonymous users** may read everything public (`/browse`, books, drafts, the
  annotations API `GET`) and **create** annotations (stored with `user_id NULL`,
  author `"anonymous"`). They cannot delete, mention, share, or hit the pipeline API.
- **Auth** is a cookie session token (`session_token`, HMAC `{user_id}.{sig}`).
  `_get_current_user` reads the cookie; `_get_api_user` also accepts an
  `Authorization: Bearer <token>` / `X-Access-Key` header where the value may be the
  shared `ACCESS_KEY` (treated as admin, `id=0`) or a user session token.
- **Annotation delete** (`DELETE /api/annotations/{id}`): allowed only for an admin,
  the annotation's own author, or the book's actual owner. Anonymous annotations
  (`user_id NULL`) are therefore deletable only by admin/owner — this is intentional
  (it closes a vandalism hole; do not "fix" the hidden Delete button for anonymous).
- **Two ownership flags, used for different things — keep them distinct:**
  - `is_owner` (lenient): logged-in AND (book unowned `owner_id IS NULL` OR owns OR admin).
    Used for owner *controls* like the Sharing panel.
  - `show_internals` / `can_moderate` (strict): admin OR actual owner only. Used to gate
    internal process detail (pipeline status, model names, source chapters, agent log,
    audience reviews) and the `/browse/book/{id}/log` route, and to gate destructive
    moderation. A legacy NULL-owner book is *public to read* but its internals show to
    admins only.
- The single production book (`EMERGENT`, id 2) has `owner_id NULL` (public). Setting an
  `owner_id` would make it private to that owner + shares AND would *block* other
  logged-in non-shared users from reading it (anonymous can still read) — so leave it
  NULL unless you also adjust `_user_can_access_book`.

## Deploy & verify
- Deploy: `railway up --detach` (Dockerfile builder; healthcheck `/health`). The project
  is linked (`railway status` → project `book-editor`, service `book-editor-app`).
- Zero-downtime: the old build serves during a deploy. To confirm the *new* build is live,
  poll for a string only present in the new code (e.g. a freshly added JS function name)
  on a served page, not just `/health` (which the old build also answers 200).
- Prod URL: `https://book-editor-app-production.up.railway.app`.

## Database access
- `railway connect Postgres` opens psql via the public proxy; it accepts piped SQL:
  `echo "SELECT ..." | railway connect Postgres`. The internal `DATABASE_URL`
  (`postgres.railway.internal`) is NOT reachable from a laptop — use `railway connect`.

## Test data cleanup
- Browser QA passes create real accounts + annotations in prod. Remove them with
  `./clean-test-data.sh` (dry-run by default; `--yes` to apply, `--include-anon` to also
  wipe anonymous annotations). It always keeps admin accounts and `KEEP_USERS`.
- A broad `--yes --include-anon` mass-delete may be blocked by the safety classifier when
  run by the agent; the human can run it directly, or delete specific rows by id/username.

## Gotchas
- `templates/` is served via a Jinja2 `TemplateResponse` compatibility shim at the top of
  `browser.py` (Starlette ≥0.29 changed the signature to `(request, name, context)`).
  Don't remove it — legacy positional `TemplateResponse(name, context)` calls rely on it.
- Pyright flags on the shim and on cross-module imports (`_get_api_user`) are pre-existing
  and benign.
- `POST /books/upload` returns 422 (not 401) when called with no file — FastAPI validates
  the required multipart field before the auth check runs. Not a security issue.
