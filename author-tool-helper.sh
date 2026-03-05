#!/usr/bin/env bash
#
# author-tool-helper.sh — Local macOS helper for the book-editor pipeline on Railway
#
# Usage:
#   ./author-tool-helper.sh <command> [args]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${GREEN}[book-editor]${NC} $*"; }
warn() { echo -e "${YELLOW}[book-editor]${NC} $*"; }
err()  { echo -e "${RED}[book-editor]${NC} $*" >&2; }

# Detect Railway service URL
get_url() {
    if [[ -f .railway-url ]]; then
        cat .railway-url
    else
        local url
        url=$(railway vars --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('RAILWAY_PUBLIC_DOMAIN',''))" 2>/dev/null || echo "")
        if [[ -n "$url" ]]; then
            echo "https://$url" > .railway-url
            echo "https://$url"
        else
            echo "http://localhost:8000"
        fi
    fi
}

# ── Watch function (reused by pipeline commands) ──
do_watch() {
    local BOOK_ID="$1"
    local URL="$(get_url)"
    log "Watching pipeline status for book $BOOK_ID (Ctrl+C to stop)..."
    echo ""
    while true; do
        # Fetch status
        local STATUS_JSON
        STATUS_JSON=$(curl -s "$URL/books/$BOOK_ID/status" 2>/dev/null || echo '{"summary":"fetch_error"}')

        local SUMMARY
        SUMMARY=$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('summary','unknown'))" 2>/dev/null || echo "unknown")

        # Clear and display
        clear
        echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════════${NC}"
        echo -e "${BOLD}  BOOK EDITOR — Pipeline Status (book $BOOK_ID)${NC}"
        echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════════${NC}"
        echo ""

        # Parse and display each pipeline
        echo "$STATUS_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
pipelines = data.get('pipelines', [])
summary = data.get('summary', 'unknown')

if not pipelines:
    print('  No pipelines started yet.')
else:
    for p in pipelines:
        stage = p.get('stage', '?')
        progress = p.get('progress', 0)
        detail = p.get('detail', '')
        error = p.get('error')
        pipeline = p.get('pipeline', '?')
        updated = str(p.get('updated_at', ''))[:19]

        # Status icon
        if 'FAIL' in stage.upper():
            icon = '❌'
        elif stage == 'complete':
            icon = '✅'
        elif stage == 'queued':
            icon = '⏳'
        else:
            icon = '🔄'

        bar_len = 30
        filled = int(progress * bar_len)
        bar = '█' * filled + '░' * (bar_len - filled)

        print(f'  {icon} {pipeline:15s} [{bar}] {progress:5.0%}  {stage}')
        if detail:
            print(f'                      {detail}')
        if error:
            print(f'  \033[0;31m  ╰─ ERROR: {error[:200]}\033[0m')
        if updated:
            print(f'                      (updated: {updated})')
        print()

print(f'  Summary: {summary}')
" 2>/dev/null || echo "$STATUS_JSON" | python3 -m json.tool 2>/dev/null || echo "  (could not parse status)"

        echo ""
        echo -e "${BLUE}--- Latest Agent Activity ---${NC}"
        curl -s "$URL/books/$BOOK_ID/interactions?limit=5" | python3 -c "
import sys, json
data = json.load(sys.stdin)
interactions = data.get('interactions', [])
if not interactions:
    print('  (no agent activity yet)')
else:
    for i in interactions[:5]:
        ts = str(i.get('created_at', ''))[:19]
        agent = i.get('agent_name', '?')
        itype = i.get('interaction_type', '?')
        content = i.get('content', '')[:100].replace(chr(10), ' ')
        print(f'  [{ts}] {agent}/{itype}: {content}...')
" 2>/dev/null || echo "  (no interactions yet)"

        echo ""
        echo -e "  ${YELLOW}Refreshing every 5s... Ctrl+C to stop${NC}"

        # Exit conditions
        if [[ "$SUMMARY" == *"ERROR"* ]]; then
            echo ""
            err "Pipeline has errors! Full status:"
            echo "$STATUS_JSON" | python3 -m json.tool
            echo ""
            err "Check Railway logs: ./author-tool-helper.sh logs"
            exit 1
        fi

        if [[ "$SUMMARY" == "idle" ]]; then
            # Check if there are completed pipelines
            local HAS_COMPLETE
            HAS_COMPLETE=$(echo "$STATUS_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
complete = [p for p in data.get('pipelines', []) if p.get('stage') == 'complete']
print('yes' if complete else 'no')
" 2>/dev/null || echo "no")
            if [[ "$HAS_COMPLETE" == "yes" ]]; then
                echo ""
                log "All pipelines complete!"
                break
            fi
        fi

        sleep 5
    done
}

case "${1:-help}" in

    setup)
        log "Setting up Railway project..."
        if ! command -v railway &>/dev/null; then
            warn "Installing Railway CLI..."
            brew install railway
        fi
        railway login
        railway init --name book-editor
        railway add --database postgres
        log "Setting environment variables..."
        echo -n "Enter your OpenRouter API key: "
        read -rs OPENROUTER_KEY
        echo
        railway vars --set OPENROUTER_API_KEY="$OPENROUTER_KEY"
        log "Setup complete. Run: ./author-tool-helper.sh deploy"
        ;;

    deploy)
        log "Deploying to Railway..."
        railway up --detach
        sleep 5
        railway vars --json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
domain = d.get('RAILWAY_PUBLIC_DOMAIN', '')
if domain:
    print(f'https://{domain}')
" > .railway-url 2>/dev/null || true
        log "Deployed! URL: $(get_url)"
        ;;

    status)
        BOOK_ID="${2:?Usage: $0 status <book_id>}"
        URL="$(get_url)"
        STATUS=$(curl -s "$URL/books/$BOOK_ID/status")
        echo "$STATUS" | python3 -m json.tool

        # Loud error check
        SUMMARY=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('summary',''))" 2>/dev/null || echo "")
        if [[ "$SUMMARY" == *"ERROR"* ]]; then
            echo ""
            err "⚠️  PIPELINE HAS ERRORS — see 'error' fields above"
        fi
        ;;

    upload)
        EPUB_FILE="${2:?Usage: $0 upload <file.epub>}"
        if [[ ! -f "$EPUB_FILE" ]]; then
            err "File not found: $EPUB_FILE"
            exit 1
        fi
        URL="$(get_url)"
        log "Uploading $EPUB_FILE..."
        curl -s -X POST "$URL/books/upload" \
            -F "file=@$EPUB_FILE" | python3 -m json.tool
        ;;

    micro)
        BOOK_ID="${2:?Usage: $0 micro <book_id>}"
        URL="$(get_url)"
        log "Starting micro-book dry run for book $BOOK_ID..."
        RESULT=$(curl -s -X POST "$URL/books/$BOOK_ID/micro")
        echo "$RESULT" | python3 -m json.tool
        echo ""
        log "Auto-watching progress..."
        sleep 2
        do_watch "$BOOK_ID"
        ;;

    full)
        BOOK_ID="${2:?Usage: $0 full <book_id>}"
        URL="$(get_url)"
        log "Starting full pipeline for book $BOOK_ID..."
        RESULT=$(curl -s -X POST "$URL/books/$BOOK_ID/full")
        echo "$RESULT" | python3 -m json.tool
        echo ""
        log "Auto-watching progress..."
        sleep 2
        do_watch "$BOOK_ID"
        ;;

    run-all)
        BOOK_ID="${2:?Usage: $0 run-all <book_id>}"
        URL="$(get_url)"
        log "Starting full orchestration (micro + full) for book $BOOK_ID..."
        RESULT=$(curl -s -X POST "$URL/books/$BOOK_ID/run-all")
        echo "$RESULT" | python3 -m json.tool
        echo ""
        log "Auto-watching progress..."
        sleep 2
        do_watch "$BOOK_ID"
        ;;

    drafts)
        BOOK_ID="${2:?Usage: $0 drafts <book_id>}"
        URL="$(get_url)"
        curl -s "$URL/books/$BOOK_ID/drafts" | python3 -m json.tool
        ;;

    draft)
        DRAFT_ID="${2:?Usage: $0 draft <draft_id>}"
        URL="$(get_url)"
        curl -s "$URL/drafts/$DRAFT_ID" | python3 -m json.tool
        ;;

    delete)
        BOOK_ID="${2:?Usage: $0 delete <book_id>}"
        URL="$(get_url)"
        log "Deleting book $BOOK_ID and all associated data..."
        curl -s -X DELETE "$URL/books/$BOOK_ID" | python3 -m json.tool
        ;;

    book)
        BOOK_ID="${2:?Usage: $0 book <book_id>}"
        URL="$(get_url)"
        curl -s "$URL/books/$BOOK_ID" | python3 -m json.tool
        ;;

    interactions)
        BOOK_ID="${2:?Usage: $0 interactions <book_id>}"
        URL="$(get_url)"
        curl -s "$URL/books/$BOOK_ID/interactions?limit=${3:-50}" | python3 -m json.tool
        ;;

    memory)
        BOOK_ID="${2:?Usage: $0 memory <book_id>}"
        URL="$(get_url)"
        curl -s "$URL/books/$BOOK_ID/judge-memory" | python3 -m json.tool
        ;;

    logs)
        log "Tailing Railway logs..."
        railway logs --tail
        ;;

    db)
        log "Connecting to Railway PostgreSQL..."
        railway connect postgres
        ;;

    ssh)
        log "Opening Railway shell..."
        railway shell
        ;;

    env)
        if [[ -n "${2:-}" ]]; then
            railway vars --set "$2"
        else
            railway vars
        fi
        ;;

    local-up)
        log "Starting local environment..."
        docker compose up -d --build
        log "Local API: http://localhost:8000"
        log "Local DB:  postgresql://postgres:postgres@localhost:5433/book_editor"
        ;;

    local-down)
        log "Stopping local environment..."
        docker compose down
        ;;

    watch)
        BOOK_ID="${2:?Usage: $0 watch <book_id>}"
        do_watch "$BOOK_ID"
        ;;

    help|*)
        echo ""
        echo "  book-editor: AI-powered book revision pipeline"
        echo ""
        echo "  SETUP:"
        echo "    ./author-tool-helper.sh setup           — Initial Railway setup"
        echo "    ./author-tool-helper.sh deploy           — Deploy to Railway"
        echo "    ./author-tool-helper.sh local-up         — Start local Docker env"
        echo "    ./author-tool-helper.sh local-down       — Stop local Docker env"
        echo ""
        echo "  PIPELINE (auto-watches after starting):"
        echo "    ./author-tool-helper.sh upload <file>    — Upload .epub"
        echo "    ./author-tool-helper.sh micro <id>       — Micro-book dry run + watch"
        echo "    ./author-tool-helper.sh full <id>        — Full editing pipeline + watch"
        echo "    ./author-tool-helper.sh run-all <id>     — Micro then full + watch"
        echo ""
        echo "  MONITORING:"
        echo "    ./author-tool-helper.sh status <id>      — Pipeline status (with errors)"
        echo "    ./author-tool-helper.sh watch <id>       — Live status dashboard"
        echo "    ./author-tool-helper.sh interactions <id> — Agent interaction log"
        echo "    ./author-tool-helper.sh memory <id>      — Judge's editorial memory"
        echo "    ./author-tool-helper.sh logs             — Tail Railway logs"
        echo ""
        echo "  RESULTS:"
        echo "    ./author-tool-helper.sh book <id>        — Book metadata + chapters"
        echo "    ./author-tool-helper.sh drafts <id>      — List all drafts"
        echo "    ./author-tool-helper.sh draft <id>       — Get draft + audience feedback"
        echo ""
        echo "  ADMIN:"
        echo "    ./author-tool-helper.sh delete <id>      — Delete a book + all data"
        echo "    ./author-tool-helper.sh db               — PostgreSQL shell (Railway)"
        echo "    ./author-tool-helper.sh ssh              — Shell into Railway container"
        echo "    ./author-tool-helper.sh env [KEY=VAL]    — View/set Railway env vars"
        echo ""
        ;;
esac
