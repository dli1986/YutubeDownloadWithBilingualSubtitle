#!/usr/bin/env bash
# YouTube Bilingual Subtitle Pipeline — OpenClaw skill wrapper
# Usage: subtitle.sh <command> [args...]

PYTHON="/mnt/c/Users/dli/Projects/MyTest/MyTest/Scripts/python.exe"
PROJECT="/mnt/c/Users/dli/Projects/MyTest/Subtitle"
VIDEOS_TXT="$PROJECT/videos.txt"

VENV_ACTIVATE="/mnt/c/Users/dli/Projects/MyTest/MyTest/Scripts/activate"

BGUTIL_URL="http://127.0.0.1:4416"
OLLAMA_URL="http://localhost:11434"

# ── helpers ──────────────────────────────────────────────────────────────────

check_prereqs() {
    local ok=1

    if curl -s --max-time 3 "$BGUTIL_URL" > /dev/null 2>&1; then
        echo "✅ bgutil PO Token server: running ($BGUTIL_URL)"
    else
        echo "⚠️  bgutil not detected at $BGUTIL_URL — attempting to start..."
        if docker run --name bgutil-provider --rm -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider; then
            echo "   Waiting for bgutil to become ready..."
            sleep 3
            if curl -s --max-time 5 "$BGUTIL_URL" > /dev/null 2>&1; then
                echo "✅ bgutil PO Token server: started ($BGUTIL_URL)"
            else
                echo "❌ bgutil started but not responding at $BGUTIL_URL"
                ok=0
            fi
        else
            echo "❌ Failed to start bgutil container."
            echo "   If a container named 'bgutil-provider' already exists, remove it first:"
            echo "     docker rm -f bgutil-provider"
            ok=0
        fi
    fi

    if curl -sf "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
        echo "✅ Ollama: running ($OLLAMA_URL)"
    else
        echo "❌ Ollama not responding at $OLLAMA_URL"
        echo "   Start Ollama on Windows host and ensure it binds to 0.0.0.0"
        ok=0
    fi

    return $((1 - ok))
}

run_python() {
    # Activate the MyTest virtual environment (WSL bash activate script)
    if [[ -f "$VENV_ACTIVATE" ]]; then
        source "$VENV_ACTIVATE"
    else
        echo "❌ Virtual env activate script not found: $VENV_ACTIVATE"
        exit 1
    fi
    cd "$PROJECT" || { echo "❌ Project dir not found: $PROJECT"; exit 1; }
    "$PYTHON" main.py "$@"
}

# ── commands ─────────────────────────────────────────────────────────────────

cmd="$1"; shift

case "$cmd" in
    check)
        check_prereqs
        ;;

    process)
        # subtitle.sh process <url> [type]
        url="$1"; type="${2:-general}"
        if [[ -z "$url" ]]; then
            echo "Usage: subtitle.sh process <youtube_url> [type]"
            exit 1
        fi
        if [[ "$url" == *"&list="* ]]; then
            echo "❌ URL contains a playlist parameter (&list=...)."
            echo "   Strip it to keep only the video ID, e.g.:"
            echo "   https://www.youtube.com/watch?v=$(echo "$url" | grep -oP '(?<=v=)[^&]+')"
            exit 1
        fi
        check_prereqs || exit 1
        echo ""
        echo "▶ Starting full pipeline: $url  (type: $type)"
        run_python --url "$url" --type "$type"
        ;;

    reprocess)
        # subtitle.sh reprocess <video_id>
        vid="$1"
        if [[ -z "$vid" ]]; then
            echo "Usage: subtitle.sh reprocess <video_id>"
            exit 1
        fi
        echo "▶ Reprocessing subtitles for: $vid (skipping download)"
        run_python --reprocess-subtitle "$vid"
        ;;

    embed)
        # subtitle.sh embed <video_id>
        vid="$1"
        if [[ -z "$vid" ]]; then
            echo "Usage: subtitle.sh embed <video_id>"
            exit 1
        fi
        echo "▶ Re-embedding subtitles for: $vid"
        run_python --embed-only "$vid"
        ;;

    queue)
        # subtitle.sh queue <url> [type] [note]
        url="$1"; type="${2:-general}"; note="${3:-}"
        if [[ -z "$url" ]]; then
            echo "Usage: subtitle.sh queue <youtube_url> [type] [note]"
            exit 1
        fi
        if [[ "$url" == *"&list="* ]]; then
            echo "❌ URL contains &list= — please strip the playlist parameter first."
            exit 1
        fi
        line="${url} ${type}${note:+ $note}"
        echo "$line" >> "$VIDEOS_TXT"
        echo "✅ Added to queue: $line"
        echo "   Run 'subtitle.sh batch' to process all queued videos."
        ;;

    batch)
        check_prereqs || exit 1
        echo ""
        echo "▶ Processing all queued videos in videos.txt"
        run_python
        ;;

    *)
        echo "YouTube Bilingual Subtitle Pipeline"
        echo ""
        echo "Usage: subtitle.sh <command> [args]"
        echo ""
        echo "Commands:"
        echo "  check                            Check bgutil + Ollama prerequisites"
        echo "  process <url> [type]             Full pipeline for a single video"
        echo "  reprocess <video_id>             Reprocess subtitles (skip download)"
        echo "  embed <video_id>                 Re-embed subtitles only"
        echo "  queue <url> [type] [note]        Add URL to videos.txt queue"
        echo "  batch                            Process all queued videos"
        echo ""
        echo "Types: baby | tech | interview | documentary | general"
        ;;
esac
