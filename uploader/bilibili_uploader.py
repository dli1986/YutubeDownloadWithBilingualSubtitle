"""
B站上传模块
负责将处理完成的双字幕视频上传到 Bilibili。

依赖：pip install bilibili-api-python
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional

import yaml

logger = logging.getLogger(__name__)


class BilibiliUploader:
    """B站视频上传器"""

    def __init__(self, upload_config_path: str = "./bili_upload.yaml"):
        with open(upload_config_path, 'r', encoding='utf-8') as f:
            self.upload_cfg: Dict = yaml.safe_load(f)

        self.cred_cfg   = self.upload_cfg.get('credentials', {})
        self.rules: Dict = {
            r['type'] if 'type' in r else k: r
            for k, r in (self.upload_cfg.get('upload_rules') or {}).items()
        }
        # upload_rules 是映射，key 就是 type
        self.rules = self.upload_cfg.get('upload_rules', {})
        self.behavior = self.upload_cfg.get('behavior', {})

        self._credential = None   # bilibili_api.Credential，懒加载
        self._loop: Optional[asyncio.AbstractEventLoop] = None  # 复用同一 loop
        self._season_section_cache: Dict[int, int] = {}  # season_id → section_id 缓存

    def _run_coroutine(self, coro):
        """
        在同步上下文中运行 async 协程，复用同一个 event loop。
        bilibili-api 内部通过 loop.call_soon / ensure_future 调度 chunk 上传，
        必须在同一个 loop 上运行才能正常 await，不能用 asyncio.run()（每次新建 loop）。
        """
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop.run_until_complete(coro)

    # ─────────────────────────────────────────────────────────────────
    # 凭证管理
    # ─────────────────────────────────────────────────────────────────

    def _cookie_file(self) -> Path:
        return Path(self.cred_cfg.get('cookie_file', './cache/bili_cookies.json'))

    def _load_credential(self):
        """从本地文件加载 Credential，文件不存在则返回 None。"""
        try:
            from bilibili_api.login_v2 import Credential
        except ImportError:
            raise ImportError("请先安装依赖：pip install bilibili-api-python")

        path = self._cookie_file()
        if not path.exists():
            return None

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return Credential(
            sessdata   = data.get('sessdata', ''),
            bili_jct   = data.get('bili_jct', ''),
            buvid3     = data.get('buvid3', ''),
            dedeuserid = data.get('dedeuserid', ''),
            ac_time_value = data.get('ac_time_value', ''),
        )

    def _save_credential(self, credential):
        """将 Credential 持久化到本地文件。"""
        path = self._cookie_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'sessdata':      credential.sessdata,
            'bili_jct':      credential.bili_jct,
            'buvid3':        credential.buvid3,
            'dedeuserid':    credential.dedeuserid,
            'ac_time_value': credential.ac_time_value,
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ 凭证已保存: {path}")

    def _get_credential(self):
        """获取凭证，不在同步上下文中做 async 刷新检查（避免 event loop 冲突）。"""
        if self._credential is not None:
            return self._credential
        cred = self._load_credential()
        if cred is None:
            raise RuntimeError(
                "未找到B站登录凭证，请先运行：python main.py --bili-login"
            )
        self._credential = cred
        return cred

    async def _ensure_credential_fresh(self):
        """在 async 上下文中检查并刷新凭证（避免 event loop 嵌套）。"""
        cred = self._get_credential()
        try:
            need_refresh = await cred.check_refresh()
            if need_refresh:
                logger.info("凭证即将过期，正在刷新...")
                await cred.refresh()
                self._save_credential(cred)
                logger.info("✓ 凭证刷新完成")
        except Exception as e:
            logger.warning(f"凭证刷新检查失败（将继续尝试）: {e}")

    # ─────────────────────────────────────────────────────────────────
    # 扫码登录（首次使用）
    # ─────────────────────────────────────────────────────────────────

    def login_qrcode(self):
        """
        终端扫码登录，生成并保存凭证文件。
        调用：python main.py --bili-login
        """
        try:
            from bilibili_api import login_v2
        except ImportError:
            raise ImportError("请先安装依赖：pip install bilibili-api-python")

        logger.info("正在生成B站登录二维码，请用B站App扫码...")

        credential = None

        async def _do_login():
            nonlocal credential
            qr = login_v2.QrCodeLogin(platform=login_v2.QrCodeLoginChannel.WEB)
            await qr.generate_qrcode()

            # 在终端打印二维码
            print(qr.get_qrcode_terminal())

            logger.info("请用B站App扫描上方二维码，等待确认...")
            while True:
                await asyncio.sleep(2)
                state = await qr.check_state()
                if state == login_v2.QrCodeLoginEvents.SCAN:
                    logger.info("已扫码，请在App上点击确认...")
                elif state == login_v2.QrCodeLoginEvents.CONF:
                    logger.info("已确认，正在获取凭证...")
                elif state == login_v2.QrCodeLoginEvents.TIMEOUT:
                    raise RuntimeError("二维码已超时，请重新运行 --bili-login")
                elif state == login_v2.QrCodeLoginEvents.DONE:
                    credential = qr.get_credential()
                    break

        asyncio.get_event_loop().run_until_complete(_do_login())

        if credential is None:
            raise RuntimeError("扫码登录失败，请重试")

        self._save_credential(credential)
        self._credential = credential
        logger.info("✓ B站登录成功，凭证已保存，后续运行无需重复登录")

    # ─────────────────────────────────────────────────────────────────
    # 元数据构建
    # ─────────────────────────────────────────────────────────────────

    def _build_meta(self, video_entry: Dict) -> Optional[Dict]:
        """
        根据 video_entry 中的 type / title / url 构建上传元数据。
        返回 None 表示该类型不需要上传。
        """
        vtype   = video_entry.get('type', 'general')
        rule    = self.rules.get(vtype)
        if rule is None:
            logger.info(f"类型 '{vtype}' 未配置上传规则，跳过上传")
            return None

        title_raw = video_entry.get('title', video_entry.get('video_id', ''))
        youtube_url = video_entry.get('url', '')
        channel = video_entry.get('channel', '')
        channel_id = video_entry.get('channel_id', '')  # from channel_scanner

        def _render(template: str) -> str:
            return (template
                    .replace('{title}',       title_raw)
                    .replace('{youtube_url}', youtube_url)
                    .replace('{channel}',     channel))

        title = _render(rule.get('title_template', '{title}'))
        # B站标题最长80字
        if len(title) > 80:
            title = title[:79] + '…'

        desc  = _render(rule.get('desc_template', '英文原视频：{youtube_url}'))

        # series_id 支持标量或 {channel_id: sid, _default: null} 映射
        raw_series = rule.get('series_id')
        if isinstance(raw_series, dict):
            series_id = raw_series.get(channel_id) or raw_series.get('_default')
        else:
            series_id = raw_series

        return {
            'title':      title,
            'desc':       desc,
            'tid':        rule.get('tid', 188),
            'tags':       rule.get('tags', ['双字幕', 'YouTube']),
            'season_id':  rule.get('season_id'),       # None = 不加合集
            'section_id': rule.get('section_id'),      # 合集默认正片分区 ID
            'series_id':  series_id,                   # None = 不加系列
            'is_reprint': rule.get('is_reprint', True),
            'source':     youtube_url if rule.get('is_reprint', True) else '',
        }

    # ─────────────────────────────────────────────────────────────────
    # 上传单个视频
    # ─────────────────────────────────────────────────────────────────

    def upload(self, video_entry: Dict) -> Optional[str]:
        """
        上传单个视频。
        video_entry 字段：
          - url          YouTube 原链接
          - type         视频类型
          - title        视频标题
          - output_video 本地视频文件路径（带硬字幕的 .mp4）
          - video_id     YouTube video_id

        返回 bvid（如 'BV1xx411c7mD'），失败返回 None。
        """
        video_path = video_entry.get('output_video')
        if not video_path or not os.path.exists(video_path):
            logger.error(f"视频文件不存在，跳过上传: {video_path}")
            return None

        meta = self._build_meta(video_entry)
        if meta is None:
            return None

        logger.info(f"开始上传到B站: {meta['title']}")
        logger.info(f"  分区tid: {meta['tid']}  合集: {meta['season_id']}")
        logger.info(f"  视频文件: {video_path}")

        max_retries = self.behavior.get('max_retries', 3)
        retry_delay = self.behavior.get('retry_delay_secs', 60)

        for attempt in range(1, max_retries + 1):
            try:
                # 复用同一个 event loop，bilibili-api 内部内翻 chunk 任务依赖持续运行的 loop
                bvid = self._run_coroutine(self._upload_async(video_path, meta))
                logger.info(f"✓ 上传成功: {meta['title']} → {bvid}")
                return bvid
            except Exception as e:
                import traceback
                logger.error(f"上传失败（第{attempt}/{max_retries}次）: {type(e).__name__}: {e}")
                logger.debug(traceback.format_exc())
                if attempt < max_retries:
                    logger.info(f"  {retry_delay}秒后重试...")
                    time.sleep(retry_delay)

        logger.error(f"上传最终失败，已重试{max_retries}次: {meta['title']}")
        return None

    def _extract_cover(self, video_path: str) -> str:
        """用 ffmpeg 截取视频第3秒帧作为封面，失败则生成纯黑占位图，确保始终返回有效路径。"""
        import subprocess, os
        cover_path = video_path + '.cover.jpg'
        # 已存在则直接复用
        if os.path.exists(cover_path):
            return cover_path
        for ss in ('3', '0'):
            try:
                result = subprocess.run(
                    ['ffmpeg', '-y', '-ss', ss, '-i', video_path,
                     '-vframes', '1', '-q:v', '2', cover_path],
                    capture_output=True, timeout=30
                )
                if result.returncode == 0 and os.path.exists(cover_path):
                    logger.debug(f"封面截取成功(ss={ss}): {cover_path}")
                    return cover_path
                logger.debug(f"ffmpeg ss={ss} 失败: {result.stderr[-200:]}")
            except Exception as e:
                logger.debug(f"封面截取异常(ss={ss}): {e}")
        # 最终降级：生成1x1纯黑 JPEG 作为占位封面
        try:
            from PIL import Image
            img = Image.new('RGB', (640, 360), color=(0, 0, 0))
            img.save(cover_path, 'JPEG')
            logger.warning(f"ffmpeg 封面失败，已用占位图替代: {cover_path}")
            return cover_path
        except Exception:
            pass
        logger.error("无法生成封面（ffmpeg 和 PIL 均失败），上传可能报错")
        return cover_path  # 返回路径让 bilibili-api 自行报错，保留完整错误信息

    async def _upload_async(self, video_path: str, meta: Dict) -> str:
        """异步上传核心逻辑（bilibili-api-python）。"""
        try:
            from bilibili_api import video_uploader
            from bilibili_api.video_uploader import VideoUploader, VideoMeta, Lines
            from bilibili_api.login_v2 import Credential
            from tqdm import tqdm
        except ImportError:
            raise ImportError("请先安装依赖：pip install bilibili-api-python tqdm")

        _pbar = None

        credential = self._get_credential()
        await self._ensure_credential_fresh()

        # original=True 自制，original=False 转载
        is_original = not meta.get('is_reprint', True)

        # 生成封面：截取视频第3秒帧，失败则使用空字符串让B站自动截取
        cover = self._extract_cover(video_path)

        # 构建 VideoMeta，然后转为 dict 以便注入 ugc_season（合集）字段
        video_meta = VideoMeta(
            title   = meta['title'],
            desc    = meta['desc'],
            tid     = meta['tid'],
            tags    = meta['tags'],
            cover   = cover,
            original= is_original,
            source  = meta.get('source', '') if not is_original else None,
        )

        # 合集通过投稿后独立 API 添加（不在 submit payload 里），这里只用标准 dict
        meta_dict = video_meta.__dict__()
        season_id  = meta.get('season_id')
        section_id = meta.get('section_id')

        _cid = None  # 从 PRE_SUBMIT 事件中捕获，供投稿后加入合集使用

        uploader = VideoUploader(
            pages      = [video_uploader.VideoUploaderPage(
                path   = video_path,
                title  = meta['title'],
            )],
            meta       = meta_dict,
            credential = credential,
            cover      = cover,
            line       = Lines.WS,
        )

        # 监听上传进度
        @uploader.on('__ALL__')
        async def _on_event(data):
            nonlocal _pbar, _cid
            event = data.get('name', '')
            raw = data.get('data', {})
            d = raw[0] if isinstance(raw, (tuple, list)) and raw else raw
            d = d if isinstance(d, dict) else {}
            if event == 'PREUPLOAD':
                logger.info("  预上传中...")
            elif event == 'PRE_CHUNK':
                if _pbar is None:
                    tot = d.get('total_chunk_count', 0)
                    _pbar = tqdm(total=tot, desc='  上传', unit='chunk', ncols=55,
                                 bar_format='{desc}: {bar} {percentage:.0f}%')
            elif event == 'AFTER_CHUNK':
                if _pbar is not None:
                    _pbar.update(1)
            elif event == 'AFTER_PAGE':
                if _pbar is not None:
                    _pbar.close()
                    _pbar = None
                logger.info("  提交投稿...")
            elif event == 'PRE_SUBMIT':
                videos = d.get('videos', [])
                if videos:
                    _cid = videos[0].get('cid')
                logger.debug(f"  [PRE_SUBMIT] tid={d.get('tid')} cid={_cid}")
            elif event not in ('PRE_PAGE', 'AFTER_SUBMIT'):
                logger.debug(f"  [event] {event} {d}")

        result = await uploader.start()
        if not result:
            raise RuntimeError(f"上传返回空结果（可能网络中断或账号受限）: {result!r}")
        bvid = result.get('bvid', '')
        aid  = result.get('aid')
        if not bvid:
            raise RuntimeError(f"上传返回无bvid: {result}")

        # 加入合集：投稿后通过独立 section/episodes/add API
        if season_id and section_id and aid and _cid:
            await self._add_to_season(aid, _cid, meta['title'], int(season_id), int(section_id), credential)
        elif season_id and not section_id:
            logger.warning(f"  bili_upload.yaml 未配置 section_id，跳过合集添加（season_id={season_id}）")
        elif season_id:
            logger.warning(f"  缺少 aid/cid，跳过合集添加（aid={aid} cid={_cid}）")

        # 加入系列（视频过审后才能操作，当前跳过由 --fix-series 补充）
        series_id = meta.get('series_id')
        if series_id:
            await self._add_to_series(bvid, series_id, credential)

        return bvid

    async def _add_to_season(self, aid: int, cid: int, title: str,
                             season_id: int, section_id: int, credential):
        """
        投稿完成后通过独立 API 将视频加入合集（ugc_season）。
        section_id 从 bili_upload.yaml 配置，对应合集下的默认"正片"分区。
        """
        import httpx, time as _t
        cookies = {
            'SESSDATA':   credential.sessdata,
            'bili_jct':   credential.bili_jct,
            'buvid3':     credential.buvid3,
            'DedeUserID': credential.dedeuserid,
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Referer':    'https://member.bilibili.com',
        }
        try:
            async with httpx.AsyncClient(cookies=cookies, headers=headers,
                                         follow_redirects=True) as client:
                r = await client.post(
                    'https://member.bilibili.com/x2/creative/web/season/section/episodes/add',
                    params={'t': int(_t.time() * 1000), 'csrf': credential.bili_jct},
                    json={
                        'sectionId': section_id,
                        'episodes':  [{'title': title, 'cid': cid, 'aid': aid}],
                        'csrf':      credential.bili_jct,
                    }
                )
                data = r.json()
                if data.get('code') == 0:
                    logger.info(f"  ✓ 已加入合集 {season_id}")
                else:
                    logger.warning(f"  加入合集失败: {data}")
        except Exception as e:
            logger.warning(f"  加入合集异常: {e}")

    async def _add_to_series(self, bvid: str, series_id: str, credential):
        """
        将已上传的视频加入指定系列。
        注意：视频需过审后才能查到 aid，建议上传后等待审核再运行 --fix-series。
        """
        try:
            from bilibili_api import video as bili_video
            from bilibili_api.channel_series import add_aids_to_series
        except ImportError:
            logger.warning("bilibili_api.channel_series 模块不可用，跳过系列添加")
            return

        try:
            v = bili_video.Video(bvid=bvid, credential=credential)
            info = await v.get_info()
            aid = info['aid']

            await add_aids_to_series(
                series_id  = int(series_id),
                aids       = [aid],
                credential = credential,
            )
            logger.info(f"  ✓ 已加入系列 {series_id}: {bvid}")
        except Exception as e:
            logger.warning(f"  加入系列失败（视频审核中或稍后手动添加）: {e}")

    # ─────────────────────────────────────────────────────────────────
    # 批量上传（由 main.py 调用）
    # ─────────────────────────────────────────────────────────────────

    def upload_pending(self, cache_manager, output_dir: str = './output') -> Dict[str, int]:
        """
        遍历 cache，找出所有处理完成但未上传的视频，依次上传。
        output_dir: 用于 fallback 重建旧视频的 output_video 路径。
        返回统计：{'uploaded': n, 'skipped': n, 'failed': n}
        """
        stats = {'uploaded': 0, 'skipped': 0, 'failed': 0}
        interval = self.behavior.get('upload_interval_secs', 30)

        pending = []
        for video_id, entry in cache_manager.cache.items():
            # 跳过处理失败的
            if entry.get('status') == 'failed':
                continue
            meta = entry.get('metadata', {})
            upload_status = meta.get('upload_status')
            # 已上传成功的跳过
            if upload_status == 'uploaded':
                stats['skipped'] += 1
                continue

            # output_video 路径：优先读缓存字段，缺失时尝试重建
            output_video = meta.get('output_video')
            if not output_video or not os.path.exists(output_video):
                output_video = self._reconstruct_output_path(
                    video_id, meta, output_dir
                )
            if not output_video:
                stats['skipped'] += 1
                continue

            # 将重建结果写回 meta，避免下次再重建
            if not meta.get('output_video'):
                meta['output_video'] = output_video

            pending.append((video_id, entry))

        if not pending:
            logger.info("没有待上传的视频")
            return stats

        logger.info(f"发现 {len(pending)} 个待上传视频")

        for idx, (video_id, entry) in enumerate(pending, 1):
            meta    = entry.get('metadata', {})
            url     = entry.get('url', '')
            vtype   = meta.get('type', 'general')
            title   = meta.get('title', video_id)
            output_video = meta.get('output_video')

            logger.info(f"\n[{idx}/{len(pending)}] 上传: {title} ({vtype})")

            video_entry = {
                'url':          url,
                'type':         vtype,
                'title':        title,
                'output_video': output_video,
                'video_id':     video_id,
                'channel':      meta.get('channel', ''),
                'channel_id':   meta.get('channel_id', ''),
            }

            bvid = self.upload(video_entry)

            if bvid:
                meta['upload_status'] = 'uploaded'
                meta['bvid']          = bvid
                import datetime
                meta['uploaded_at']   = datetime.datetime.now().isoformat()
                cache_manager._save_cache()
                stats['uploaded'] += 1
                if idx < len(pending):
                    logger.info(f"  等待 {interval}秒 后处理下一个...")
                    time.sleep(interval)
            else:
                meta['upload_status'] = 'upload_failed'
                cache_manager._save_cache()
                stats['failed'] += 1

        logger.info(
            f"\nB站上传完成：成功 {stats['uploaded']} / "
            f"失败 {stats['failed']} / 跳过 {stats['skipped']}"
        )
        return stats

    def fix_series(self, cache_manager) -> None:
        """
        对所有已上传但未完成系列归档（series_fixed!=True）的视频，
        补充调用 _add_to_series。视频必须已过审才能查到 aid。
        运行时机：上传后次日（审核一般1-24小时）。
        """
        import asyncio as _aio
        credential = self._get_credential()
        fixed = 0
        failed = 0
        for video_id, entry in cache_manager.cache.items():
            meta = entry.get('metadata', {})
            if meta.get('upload_status') != 'uploaded':
                continue
            if meta.get('series_fixed'):
                continue
            bvid = meta.get('bvid', '')
            if not bvid or bvid == 'manual':
                continue
            vtype      = meta.get('type', 'general')
            channel_id = meta.get('channel_id') or ''
            rule = self.rules.get(vtype)
            if not rule:
                continue
            raw_series = rule.get('series_id')
            if isinstance(raw_series, dict):
                series_id = (raw_series.get(channel_id) if channel_id else None) \
                            or raw_series.get('_default')
            else:
                series_id = raw_series
            if not series_id:
                continue

            logger.info(f"补充系列 {series_id}: {bvid} ({meta.get('title','')[:40]})")
            try:
                self._run_coroutine(
                    self._add_to_series(bvid, str(series_id), credential)
                )
                meta['series_fixed'] = True
                cache_manager._save_cache()
                fixed += 1
            except Exception as e:
                logger.warning(f"  失败: {e}")
                failed += 1

        logger.info(f"fix_series 完成：成功 {fixed} / 失败 {failed}")

    def _reconstruct_output_path(self, video_id: str, meta: dict, output_dir: str):
        """
        对旧版 cache（缺少 output_video 字段）尝试推算输出视频路径。
        路径规则：output_dir/<type>/<video_id>/<title>.bilingual.mp4
        必须与 core/utils.py sanitize_filename() + get_output_path() 保持一致。
        """
        title  = meta.get('title', '')
        vtype  = meta.get('type', 'general')
        if not title:
            return None
        # 与 sanitize_filename() 保持完全一致：移除非法字符 + 截断200字
        import re as _re
        safe_title = _re.sub(r'[<>:"/\\|?*]', '', title)
        if len(safe_title) > 200:
            safe_title = safe_title[:200]
        path = os.path.join(output_dir, vtype, video_id, f"{safe_title}.bilingual.mp4")
        if os.path.exists(path):
            logger.debug(f"  fallback output_video 重建成功: {path}")
            return path
        return None
