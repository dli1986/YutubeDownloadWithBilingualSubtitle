---
name: youtube-subtitle
description: Download YouTube videos and generate bilingual (English + Chinese) subtitles with embedded hard-subs. Use when: user shares a YouTube URL and wants Chinese subtitles, bilingual subtitles, or 双语字幕. Also handles reprocessing subtitles or re-embedding for already-downloaded videos. Manages a videos.txt queue for batch processing. NOT for: non-YouTube URLs, audio-only extraction, live streams.
homepage: https://github.com/yt-dlp/yt-dlp
metadata: {"openclaw":{"emoji":"🎬","requires":{"bins":["python3","ffmpeg","curl","docker"]}}}
---

# YouTube Bilingual Subtitle Generator

Download a YouTube video, extract or transcribe English subtitles, translate to
Chinese via local Ollama LLM, merge into a bilingual SRT, and burn them into
the video with ffmpeg (GPU-accelerated).

## When to Use

✅ USE this skill when:

- User shares a YouTube URL and asks for subtitles / 字幕 / 双语字幕
- User asks to download a YouTube video with Chinese translation
- User wants to reprocess, fix, or retranslate subtitles for a video already downloaded
- User asks to re-embed subtitles into an already-processed video
- User wants to add a YouTube URL to the processing queue (videos.txt)
- User says "帮我下载这个视频并生成双语字幕"

## When NOT to Use

❌ DON'T use this skill when:

- Non-YouTube URLs (Bilibili, Vimeo, Twitter, etc.) → use yt-dlp directly
- User only wants to watch online → just share the link
- URL contains `&list=` playlist parameter → strip it first, keep only `?v=VIDEO_ID`
- bgutil service is not running → check prerequisites first and ask user to start it

## Prerequisites

Always check before processing:

```bash
{baseDir}/scripts/subtitle.sh check
```

If bgutil is down, start it (find the container name first):

```bash
docker ps -a --filter "ancestor=ghcr.io/bgutil/bgutil" --format "{{.Names}}"
docker start <container-name>
```

If Ollama is down (runs on Windows host, accessible from WSL):

```bash
# Ollama should auto-start on Windows; check from WSL:
curl -s http://localhost:11434/api/tags | python3 -m json.tool | head -20
```

## Operation Modes

### 1. Full Pipeline — single video (recommended)

```bash
{baseDir}/scripts/subtitle.sh process "https://www.youtube.com/watch?v=VIDEO_ID" TYPE
```

`TYPE` must match a key in `config.yaml → video_types`:

| Type | Use for |
| --- | --- |
| `baby` | Children's songs, educational kids content |
| `tech` | Programming, AI, engineering talks |
| `interview` | Podcasts, conversations, interviews |
| `documentary` | Factual / nature / history content |
| `general` | Anything else |

Example:

```bash
{baseDir}/scripts/subtitle.sh process "https://www.youtube.com/watch?v=qoinGjj60Fo" interview
```

### 2. Queue + Batch Processing

Add a URL to the queue without processing immediately:

```bash
{baseDir}/scripts/subtitle.sh queue "https://www.youtube.com/watch?v=VIDEO_ID" TYPE "optional note"
```

Then process all queued videos:

```bash
{baseDir}/scripts/subtitle.sh batch
```

### 3. Reprocess Subtitles Only (skip re-download)

Use when subtitle quality needs fixing, translation model changed, or
`vtt_to_srt()` logic was updated. The video file stays untouched.

```bash
{baseDir}/scripts/subtitle.sh reprocess VIDEO_ID
```

Example: `{baseDir}/scripts/subtitle.sh reprocess VnxyEGCIi2Y`

### 4. Re-embed Subtitles Only

Use when only the ffmpeg burn-in step failed (subtitles are already correct).

```bash
{baseDir}/scripts/subtitle.sh embed VIDEO_ID
```

## Output Structure

```
output/
└── {type}/
    └── {video_id}/
        ├── Title.bilingual.srt   # standalone bilingual subtitle file
        └── Title.bilingual.mp4   # video with hard-coded bilingual subtitles

cache/
└── {video_id}/
    ├── {video_id}.mp4            # original downloaded video (kept)
    ├── {video_id}.en.vtt         # raw YouTube subtitle (source of truth)
    ├── subtitle.en.srt           # cleaned English SRT
    └── subtitle.zh.srt           # Chinese translation
```

Output path on Windows: `C:\Users\dli\Projects\MyTest\Subtitle\output\`

## videos.txt Format

One URL per line, space-separated fields. `#` lines are comments.

```
https://www.youtube.com/watch?v=xxxxx  baby      儿童学习视频
https://www.youtube.com/watch?v=yyyyy  tech      Python tutorial
https://www.youtube.com/watch?v=zzzzz  interview DeepMind Podcast ep1
```

- **No `&list=` parameters** — strip playlist ID, keep only `?v=VIDEO_ID`
- Already-processed videos are skipped automatically (cache tracks state)

## Notes

- Full pipeline: 30–90 min per video (download rate-limited to ~1.5MB/s + 20-45s sleep between steps; translation ~15 min via local Ollama)
- VIDEO_ID = the `?v=` part of a YouTube URL (e.g. `VnxyEGCIi2Y`)
- Pipeline runs on **Windows** via WSL interop (`/mnt/c/...`); GPU encoding uses NVENC if available
- If download fails mid-way, re-run the same command — yt-dlp resumes from partial files
- Subtitles are sourced from YouTube's auto-captions (preferred) or Whisper ASR (fallback)
