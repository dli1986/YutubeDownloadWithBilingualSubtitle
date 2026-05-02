"""
Channel Scanner — 启动时扫描订阅频道，发现新的、符合时长要求的视频。

用法（channels.yaml）：
  channels:
    - id: "UCxxxxxxxxxxxxxx"      # YouTube channel ID 或 @handle
      type: "tech"                # 对应 config.yaml video_types 的键
      min_duration_mins: 20       # 最短时长（分钟），默认 20
      # archive_file 可不填，默认 ./cache/channel_<id>.archive

增量策略（两阶段）：
  第一次运行（无 archive 文件）：
    → 拉取频道当前所有视频 ID，全部写入 archive，不加入处理队列。
    → 效果：以"今天"为起点，此后才发布的视频才会被自动发现。

  后续每次运行（archive 已存在）：
    → 拉取最新 200 条元数据，跳过 archive 中已有的 ID。
    → 返回的新 ID 立即追加写入 archive（防止下次重复入队）。
    → 将新视频加入处理队列，走完整下载→转录→翻译→嵌入流程。

两级去重：
  archive  → "已扫描/已入队"（即使处理失败也不会重复入队）
  cache    → "已成功完成全流程"（由 cache_manager 管理）
"""

import os
import logging
import yaml
import yt_dlp
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)

# yt-dlp 内置时长过滤表达式
_MIN_SECS = lambda mins: mins * 60


def _inject_cookies(opts: dict, config: dict) -> dict:
    """将 cookies 配置注入 yt-dlp opts（复用，避免重复代码）。"""
    dl_cfg = config.get('downloader', {})
    browser = dl_cfg.get('cookies_from_browser')
    if browser:
        profile = dl_cfg.get('cookies_from_browser_profile')
        opts['cookiesfrombrowser'] = (browser, profile, None, None) if profile else (browser,)
    elif dl_cfg.get('cookies_file'):
        opts['cookiefile'] = dl_cfg['cookies_file']
    return opts


def _build_scan_opts(config: dict, archive_file: str, min_secs: int) -> dict:
    """
    构造增量扫描用 yt-dlp opts：
    - extract_flat=True：仅读元数据，不进入单视频页，极快
    - download_archive：yt-dlp 读取此文件来过滤已知 ID（不写入）
    - match_filter：在 yt-dlp 层面预过滤时长，减少返回条目
    - playlistend=200：只看最新 200 条，正常频道更新频率下足够覆盖两次运行之间的新内容
    """
    opts = {
        'quiet':            True,
        'no_warnings':      True,
        'extract_flat':     True,
        'skip_download':    True,
        'download_archive': archive_file,
        'match_filter':     yt_dlp.utils.match_filter_func(f"duration >= {min_secs}"),
        'playlistend':      200,
    }
    return _inject_cookies(opts, config)


def _mark_existing_as_seen(config: dict, url: str, archive_file: str) -> int:
    """
    【首次运行 bootstrap】
    扫描频道当前所有视频，将 ID 全部写入 archive，但不加入处理队列。
    这样"今天"就成为追踪起点，此后发布的视频才会在下次运行时被发现。

    不加 match_filter / playlistend 限制，尽量覆盖全部历史，
    避免遗漏导致历史视频在下次运行时意外入队。
    """
    opts = {
        'quiet':        True,
        'no_warnings':  True,
        'extract_flat': True,
        'skip_download': True,
        'playlistend':  2000,   # 足够覆盖绝大多数频道的全部历史
    }
    _inject_cookies(opts, config)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = (info or {}).get('entries') or []
        count = 0
        with open(archive_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                if entry and entry.get('id'):
                    f.write(f"youtube {entry['id']}\n")
                    count += 1
        return count
    except Exception as e:
        logger.warning(f"  初始化 archive 失败: {e}")
        return 0


def _append_to_archive(archive_file: str, video_ids: List[str]) -> None:
    """
    将本次扫描发现的新视频 ID 追加写入 archive。

    重要：yt-dlp 在 download=False 时只读 archive、不写入。
    必须手动追加，否则下次运行会重复返回同一批视频。
    """
    with open(archive_file, 'a', encoding='utf-8') as f:
        for vid_id in video_ids:
            f.write(f"youtube {vid_id}\n")


def _channel_url(channel_id: str) -> str:
    """将 channel_id 或 @handle 转为视频列表 URL。"""
    if channel_id.startswith('@'):
        return f"https://www.youtube.com/{channel_id}/videos"
    elif channel_id.startswith('UC'):
        return f"https://www.youtube.com/channel/{channel_id}/videos"
    else:
        # 兼容直接传 URL 的情况
        return channel_id


def scan_channels(config: dict, channels_file: str = './channels.yaml') -> List[Dict]:
    """
    扫描所有订阅频道，返回新视频的 video_entry 列表。
    每个 entry 格式与 videos.txt 解析结果一致：
      {'url': ..., 'type': ..., 'note': ...}

    两阶段增量策略：
      首次（无 archive）→ bootstrap，将现有视频全部标记为已见，返回 []
      后续运行       → 只返回 archive 中没有的新视频，并立即写入 archive
    """
    if not os.path.exists(channels_file):
        logger.debug(f"channels.yaml 不存在，跳过频道扫描: {channels_file}")
        return []

    try:
        with open(channels_file, encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"channels.yaml 解析失败: {e}")
        return []

    channels = data.get('channels', [])
    if not channels:
        return []

    cache_dir = config.get('cache', {}).get('cache_dir', './cache')
    Path(cache_dir).mkdir(parents=True, exist_ok=True)

    new_entries: List[Dict] = []

    for ch in channels:
        ch_id = str(ch.get('id', '')).strip()
        if not ch_id:
            logger.warning("channels.yaml 中有条目缺少 id，跳过")
            continue

        ch_type  = ch.get('type', 'general')
        min_mins = int(ch.get('min_duration_mins', 15))
        min_secs = _MIN_SECS(min_mins)

        # 每个 channel 独立一个 archive 文件
        safe_id      = ch_id.lstrip('@').replace('/', '_')
        archive_file = ch.get('archive_file') or \
                       os.path.join(cache_dir, f"channel_{safe_id}.archive")

        url = _channel_url(ch_id)

        # ── 首次运行：archive 不存在 ────────────────────────────────────
        if not os.path.exists(archive_file):
            logger.info(f"  首次订阅 {ch_id}：标记当前所有历史视频为已见，今后仅追踪新视频...")
            count = _mark_existing_as_seen(config, url, archive_file)
            logger.info(f"  初始化完成（标记了 {count} 条历史视频），下次运行起开始追踪新视频")
            continue   # 本次不入队任何视频

        # ── 后续运行：archive 已存在，只看新视频 ────────────────────────
        logger.info(f"  扫描频道: {ch_id}  type={ch_type}  min={min_mins}min")
        try:
            opts = _build_scan_opts(config, archive_file, min_secs)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                logger.warning(f"  频道 {ch_id} 返回空结果")
                continue

            entries    = info.get('entries') or []
            found_ids  = []

            for entry in entries:
                if not entry:
                    continue
                vid_id   = entry.get('id')
                if not vid_id:
                    continue
                duration = entry.get('duration') or 0
                # flat-playlist 下私有/直播视频 duration 可能为 None，跳过
                if duration and duration < min_secs:
                    continue
                vid_url = f"https://www.youtube.com/watch?v={vid_id}"
                new_entries.append({
                    'url':        vid_url,
                    'type':       ch_type,
                    'channel_id': ch_id,
                    'note': f"channel:{ch_id}  title:{entry.get('title', '')}",
                })
                found_ids.append(vid_id)

            # !! 关键：yt-dlp 在 download=False 时不写 archive，必须手动追加
            # 立即写入，确保下次运行不会重复入队（即使本次处理失败）
            if found_ids:
                _append_to_archive(archive_file, found_ids)

            logger.info(f"  → {ch_id}: 新增 {len(found_ids)} 个视频入队")

        except Exception as e:
            logger.warning(f"  扫描频道 {ch_id} 失败: {e}")
            continue

    logger.info(f"频道扫描完成，共新增 {len(new_entries)} 个视频入队")
    return new_entries
