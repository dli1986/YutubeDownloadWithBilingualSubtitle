"""
cache_cleanup.py — 清理已成功上传视频的 cache 原始文件

策略：
  删除：<video_id>.mp4, *.json3, *.vtt, *.srv3  (占用主要空间)
  保留：subtitle.en.srt, subtitle.zh.srt        (reprocess-subtitle 的 fallback 源)
  保留：processed_videos.json, bili_cookies.json, channel_*.archive

使用：
  python tests/tools/cache_cleanup.py           # dry-run（只显示，不删除）
  python tests/tools/cache_cleanup.py --confirm # 真实执行
"""
import sys, os, json, argparse
from pathlib import Path

CACHE_DIR = Path('./cache')
DB_FILE   = CACHE_DIR / 'processed_videos.json'

# 删除目标：按扩展名匹配
DELETE_EXTS = {'.mp4', '.json3', '.vtt', '.srv3', '.webm', '.m4a'}

def main():
    parser = argparse.ArgumentParser(description='清理已上传视频的 cache 原始文件')
    parser.add_argument('--confirm', action='store_true', help='实际执行删除（默认 dry-run）')
    parser.add_argument('--all-processed', action='store_true',
                        help='清理所有已处理视频（默认只清理 upload_status=uploaded 的）')
    args = parser.parse_args()
    dry_run = not args.confirm

    if dry_run:
        print('[DRY-RUN] 仅预览，不删除任何文件。加 --confirm 参数才真实执行。\n')

    with open(DB_FILE, encoding='utf-8') as f:
        cache = json.load(f)

    to_delete = []       # (Path, size_bytes)
    kept_srt  = []
    skipped_no_folder = 0
    skipped_not_uploaded = 0

    for vid_id, entry in cache.items():
        meta = entry.get('metadata', {})
        upload_status = meta.get('upload_status')

        # 默认只清理已上传的
        if not args.all_processed and upload_status != 'uploaded':
            skipped_not_uploaded += 1
            continue

        vdir = CACHE_DIR / vid_id
        if not vdir.is_dir():
            skipped_no_folder += 1
            continue

        for f in vdir.iterdir():
            if f.suffix.lower() in DELETE_EXTS:
                to_delete.append((f, f.stat().st_size))
            elif f.name.startswith('subtitle.') and f.suffix == '.srt':
                kept_srt.append(f)

    # 按大小排序展示
    to_delete.sort(key=lambda x: -x[1])
    total_bytes = sum(s for _, s in to_delete)

    print(f'待删除文件：{len(to_delete)} 个，合计 {total_bytes/1024**3:.2f} GB')
    print(f'保留 SRT 文件：{len(kept_srt)} 个')
    print(f'跳过（未上传）：{skipped_not_uploaded} 个视频')
    print(f'跳过（无 cache 文件夹）：{skipped_no_folder} 个')
    print()

    # 展示 Top 20 大文件
    print(f'{"文件":<60} {"大小(MB)":>10}')
    print('-' * 72)
    for f, sz in to_delete[:25]:
        rel = str(f.relative_to(CACHE_DIR))
        print(f'{rel:<60} {sz/1024**2:>10.1f}')
    if len(to_delete) > 25:
        print(f'  ... ({len(to_delete)-25} more files not shown)')
    print('-' * 72)

    if dry_run:
        print(f'\n[DRY-RUN] 如确认无误，运行：python tests/tools/cache_cleanup.py --confirm')
        return

    # 实际删除
    print(f'\n开始删除 {len(to_delete)} 个文件...')
    deleted = 0
    errors  = 0
    for f, _ in to_delete:
        try:
            f.unlink()
            deleted += 1
        except Exception as e:
            print(f'  ERROR: {f}: {e}')
            errors += 1

    # 清理空的 video 子目录（只剩 SRT 的不算空，只有空目录才删）
    emptied = 0
    for vid_id in cache:
        vdir = CACHE_DIR / vid_id
        if vdir.is_dir() and not any(vdir.iterdir()):
            vdir.rmdir()
            emptied += 1

    print(f'\n完成：删除 {deleted} 个文件，{emptied} 个空目录，{errors} 个错误')
    print(f'释放空间约 {total_bytes/1024**3:.2f} GB')

if __name__ == '__main__':
    os.chdir(Path(__file__).parent.parent.parent)  # 确保从项目根目录运行
    main()
