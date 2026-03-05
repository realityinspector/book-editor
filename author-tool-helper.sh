#!/usr/bin/env bash
#
# author-tool-helper.sh — Local macOS helper for the book-editor pipeline on Railway
#
# Usage:
#   ./author-tool-helper.sh <command> [args]
#
# Commands:
#   setup          — Initial Railway project setup
#   deploy         — Deploy to Railway
#   status         — Get pipeline status for a book
#   upload <file>  — Upload an .epub and ingest it
#   micro <id>     — Run micro-book dry run
#   full <id>      — Run full pipeline
#   run-all <id>   — Run micro then full pipeline
#   drafts <id>    — List all drafts for a book
#   draft <id>     — Get a specific draft with feedback
#   interactions <id> — View agent interaction log
#   memory <id>    — View judge's accumulated memory
#   logs           — Tail Railway logs
#   db             — Open psql to Railway database
#   ssh            — SSH into Railway service
#   env            — Show/set environment variables
#   local-up       — Start local docker-compose
#   local-down     — Stop local docker-compose

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[book-editor]${NC} $*"; }
warn() { echo -e "${YELLOW}[book-editor]${NC} $*"; }
err()  { echo -e "${RED}[book-editor]${NC} $*" >&2; }

# Detect Railway service URL
get_url() {
    if [[ -f .railway-url ]]; then
        cat .railway-url
    else
        # Try to get from Railway
        local url
        url=$(railway variables --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('RAILWAY_PUBLIC_DOMAIN',''))" 2>/dev/null || echo "")
        if [[ -n "$url" ]]; then
            echo "https://$url" > .railway-url
            echo "https://$url"
        else
            echo "http://localhost:8000"
        fi
    fi
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
        railway add --plugin postgresql
        log "Setting environment variables..."
        echo -n "Enter your OpenRouter API key: "
        read -rs OPENROUTER_KEY
        echo
        railway variables set OPENROUTER_API_KEY="$OPENROUTER_KEY"
        log "Setup complete. Run: ./author-tool-helper.sh deploy"
        ;;

    deploy)
        log "Deploying to Railway..."
        railway up --detach
        sleep 5
        # Capture the URL
        railway variables --json 2>/dev/null | python3 -c "
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
        curl -s "$URL/books/$BOOK_ID/status" | python3 -m json.tool
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
        curl -s -X POST "$URL/books/$BOOK_ID/micro" | python3 -m json.tool
        ;;

    full)
        BOOK_ID="${2:?Usage: $0 full <book_id>}"
        URL="$(get_url)"
        log "Starting full pipeline for book $BOOK_ID..."
        curl -s -X POST "$URL/books/$BOOK_ID/full" | python3 -m json.tool
        ;;

    run-all)
        BOOK_ID="${2:?Usage: $0 run-all <book_id>}"
        URL="$(get_url)"
        log "Starting full orchestration (micro + full) for book $BOOK_ID..."
        curl -s -X POST "$URL/books/$BOOK_ID/run-all" | python3 -m json.tool
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
            railway variables set "$2"
        else
            railway variables
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
        URL="$(get_url)"
        log "Watching pipeline status for book $BOOK_ID (Ctrl+C to stop)..."
        while true; do
            clear
            echo -e "${BLUE}=== Book Editor Pipeline Status ===${NC}"
            echo ""
            curl -s "$URL/books/$BOOK_ID/status" | python3 -m json.tool
            echo ""
            echo -e "${BLUE}--- Latest Interactions ---${NC}"
            curl -s "$URL/books/$BOOK_ID/interactions?limit=5" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for i in data.get('interactions', [])[:5]:
    print(f\"  [{i['agent_name']}] {i['interaction_type']}: {i['content'][:80]}...\")
" 2>/dev/null || echo "  (no interactions yet)"
            sleep 10
        done
        ;;

    help|*)
        echo ""
        echo "  book-editor: AI-powered book revision pipeline"
        echo ""
        echo "  SETUP:"
        echo "    ./author-tool-helper.sh setup        — Initial Railway setup"
        echo "    ./author-tool-helper.sh deploy        — Deploy to Railway"
        echo "    ./author-tool-helper.sh local-up      — Start local Docker env"
        echo "    ./author-tool-helper.sh local-down    — Stop local Docker env"
        echo ""
        echo "  PIPELINE:"
        echo "    ./author-tool-helper.sh upload <file>   — Upload .epub"
        echo "    ./author-tool-helper.sh micro <id>      — Micro-book dry run"
        echo "    ./author-tool-helper.sh full <id>       — Full editing pipeline"
        echo "    ./author-tool-helper.sh run-all <id>    — Micro then full pipeline"
        echo ""
        echo "  MONITORING:"
        echo "    ./author-tool-helper.sh status <id>         — Pipeline status"
        echo "    ./author-tool-helper.sh watch <id>          — Live status watch"
        echo "    ./author-tool-helper.sh interactions <id>   — Agent interaction log"
        echo "    ./author-tool-helper.sh memory <id>         — Judge memory"
        echo "    ./author-tool-helper.sh logs                — Railway logs"
        echo ""
        echo "  RESULTS:"
        echo "    ./author-tool-helper.sh book <id>       — Book metadata"
        echo "    ./author-tool-helper.sh drafts <id>     — List drafts"
        echo "    ./author-tool-helper.sh draft <id>      — Get draft + feedback"
        echo ""
        echo "  ADMIN:"
        echo "    ./author-tool-helper.sh db              — PostgreSQL shell"
        echo "    ./author-tool-helper.sh ssh             — Railway shell"
        echo "    ./author-tool-helper.sh env [KEY=VAL]   — View/set env vars"
        echo ""
        ;;
esac
