"""
视频下载模块
使用yt-dlp下载YouTube视频和字幕，包含延迟机制避免SABR限制
"""

import os
import time
import random
import logging
from pathlib import Path
from typing import Dict, Optional
import yt_dlp


logger = logging.getLogger(__name__)


class VideoDownloader:
    """YouTube视频下载器"""
    
    def __init__(self, config: Dict):
        self.config = config.get('downloader', {})
        self.cache_dir = config.get('cache', {}).get('cache_dir', './cache')
        self.download_delay = self.config.get('download_delay', 300)
        self.min_delay = self.config.get('min_delay', 30)   # 随机延迟下限（秒）
        self.max_delay = self.config.get('max_delay', 90)   # 随机延迟上限（秒）
        self.max_retries = self.config.get('max_retries', 3)
        self.retry_delay = 30  # 失败重试延迟（秒）
        self.last_download_time = 0
        
        # 确保缓存目录存在
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)

    def _apply_cookies(self, ydl_opts: dict) -> dict:
        """
        注入认证凭据（优先直接读浏览器，其次 cookies 文件）
        直接读浏览器：无需导出文件，yt-dlp 运行时临时读取，不落盘
        """
        browser = self.config.get('cookies_from_browser')  # e.g. 'firefox'
        if browser:
            profile = self.config.get('cookies_from_browser_profile')  # profile 名或完整路径
            if profile:
                ydl_opts['cookiesfrombrowser'] = (browser, profile, None, None)
                logger.debug(f"使用浏览器 cookies: {browser} / profile={profile}")
            else:
                ydl_opts['cookiesfrombrowser'] = (browser,)
                logger.debug(f"使用浏览器 cookies: {browser} (默认 profile)")
        elif self.config.get('cookies_file'):
            ydl_opts['cookiefile'] = self.config['cookies_file']
            logger.debug(f"使用 cookies 文件: {self.config['cookies_file']}")
        return ydl_opts

    def _wait_if_needed(self):
        """
        随机等待30-90秒（方案C），模拟人类行为，降低速率限制触发概率
        """
        current_time = time.time()
        time_since_last = current_time - self.last_download_time

        jitter_delay = random.uniform(self.min_delay, self.max_delay)

        if time_since_last < jitter_delay:
            wait_time = jitter_delay - time_since_last
            logger.info(f"等待 {wait_time:.0f} 秒避免速率限制 (随机间隔 {jitter_delay:.0f}s)...")
            time.sleep(wait_time)

        self.last_download_time = time.time()
    
    def get_video_info(self, url: str) -> Optional[Dict]:
        """
        获取视频信息而不下载
        """
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            # mweb: 无需 PO Token，无 SABR，支持 1080p；web 作为 fallback
            # bgutil 插件装好后自动提供 PO Token，无需额外配置（默认连 127.0.0.1:4416）
            'extractor_args': {
                'youtube': {
                    'player_client': ['mweb', 'web'],
                }
            },
        }
        
        if self.config.get('proxy'):
            ydl_opts['proxy'] = self.config['proxy']
        self._apply_cookies(ydl_opts)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return {
                    'id': info.get('id'),
                    'title': info.get('title'),
                    'duration': info.get('duration'),
                    'description': info.get('description'),
                    'uploader': info.get('uploader'),
                    'uploader_id': info.get('uploader_id'),
                    'upload_date': info.get('upload_date'),
                }
        except Exception as e:
            logger.error(f"获取视频信息失败: {e}")
            return None
    
    def download_video(self, url: str, output_path: str) -> bool:
        """
        下载视频文件
        使用配置的视频质量设置
        """
        self._wait_if_needed()
        
        # 从配置获取视频质量设置
        video_quality = self.config.get('video_quality', 'bestvideo+bestaudio/best')
        
        # 根据配置构建format字符串
        if video_quality == '1080p':
            # 1080p: 允许任何格式(mp4/webm)，选择最高质量
            format_str = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'
        elif video_quality == '720p':
            format_str = 'bestvideo[height<=720]+bestaudio/best[height<=720]'
        elif video_quality == '480p':
            format_str = 'bestvideo[height<=480]+bestaudio/best[height<=480]'
        elif video_quality == 'best':
            # 最佳质量：不限制格式和分辨率
            format_str = 'bestvideo+bestaudio/best'
        else:
            # 自定义格式
            format_str = video_quality
        
        # yt-dlp选项
        ydl_opts = {
            'format': format_str,
            'outtmpl': output_path,
            'quiet': False,
            'no_warnings': False,
            # mweb: 无需 PO Token，无 SABR，支持 1080p；web 作为 fallback
            'extractor_args': {
                'youtube': {
                    'player_client': ['mweb', 'web'],
                }
            },
            # 合并视频和音频
            'merge_output_format': 'mp4',  # 最终输出为mp4
            # 重试选项（增强以应对403错误）
            'retries': 10,
            'fragment_retries': 10,
            'file_access_retries': 5,
            'socket_timeout': 30,
            # 伪装成真实浏览器，降低被检测概率
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            },
            # 限速以避免触发反爬虫（可选，降低下载速度但更安全）
            # 'ratelimit': 5000000,  # 5MB/s，取消注释以启用
        }
        
        if self.config.get('proxy'):
            ydl_opts['proxy'] = self.config['proxy']
        self._apply_cookies(ydl_opts)

        for attempt in range(self.max_retries):
            try:
                logger.info(f"下载视频 (尝试 {attempt + 1}/{self.max_retries})...")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                
                # 验证文件是否真的下载成功
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    logger.info(f"视频下载成功: {output_path}")
                    return True
                else:
                    raise Exception("下载的文件为空")
                    
            except Exception as e:
                logger.error(f"视频下载失败 (尝试 {attempt + 1}): {str(e)}")
                if attempt < self.max_retries - 1:
                    # 403错误时延长等待时间，让YouTube "冷却"
                    wait_time = self.retry_delay * (2 ** attempt)  # 指数退避: 30s, 60s, 120s
                    logger.info(f"等待{wait_time}秒后重试... (403错误需要更长冷却时间)")
                    time.sleep(wait_time)
        
        return False
    
    def download_subtitles(self, url: str, output_dir: str, langs: list = ['en']) -> Dict[str, Optional[str]]:
        """
        下载字幕文件
        返回: {'en': 'path/to/en.vtt', 'zh-Hans': 'path/to/zh.vtt'}
        """
        self._wait_if_needed()
        
        subtitle_paths = {}
        
        for lang in langs:
            ydl_opts = {
                'skip_download': True,
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': [lang],
                # json3 提供逐字时间戳，可重建无重叠句子段；srv3/vtt 作为降级备选
                'subtitlesformat': 'json3/srv3/vtt',
                'outtmpl': os.path.join(output_dir, '%(id)s.%(ext)s'),
                'quiet': False,
                # mweb: 无需 PO Token，无 SABR；web 作为 fallback
                'extractor_args': {
                    'youtube': {
                        'player_client': ['mweb', 'web'],
                    }
                },
            }
            
            if self.config.get('proxy'):
                ydl_opts['proxy'] = self.config['proxy']
            self._apply_cookies(ydl_opts)

            for attempt in range(self.max_retries):
                try:
                    logger.info(f"下载 {lang} 字幕 (尝试 {attempt + 1}/{self.max_retries})...")
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        video_id = info['id']
                        
                        # 检查可用的字幕类型
                        available_subs = info.get('subtitles', {})
                        auto_subs = info.get('automatic_captions', {})
                        
                        # 查找生成的字幕文件（srv3 格式）
                        subtitle_file = os.path.join(output_dir, f"{video_id}.{lang}.srv3")
                        # 兼容回退：若 srv3 不可用时 yt-dlp 可能退回 vtt
                        if not os.path.exists(subtitle_file):
                            subtitle_file = os.path.join(output_dir, f"{video_id}.{lang}.vtt")
                        
                        if os.path.exists(subtitle_file):
                            # 判断是手动字幕还是自动字幕
                            is_manual = lang in available_subs
                            is_auto = lang in auto_subs
                            
                            sub_type = "人工字幕" if is_manual else ("自动字幕(CC)" if is_auto else "字幕")
                            subtitle_paths[lang] = subtitle_file
                            logger.info(f"✓ {lang} {sub_type}下载成功: {subtitle_file}")
                            break
                        else:
                            logger.warning(f"✗ 该视频不提供 {lang} 字幕（无人工字幕，也无自动生成）")
                            subtitle_paths[lang] = None
                            break
                
                except Exception as e:
                    logger.error(f"{lang} 字幕下载失败 (尝试 {attempt + 1}): {e}")
                    if attempt < self.max_retries - 1:
                        time.sleep(30)
                    else:
                        subtitle_paths[lang] = None
            
            # 每个字幕下载后等待，避免连续请求
            if lang != langs[-1]:  # 不是最后一个
                logger.info(f"等待 {self.download_delay} 秒后下载下一个字幕...")
                time.sleep(self.download_delay)
        
        return subtitle_paths
    
    def download_all(self, url: str, video_id: str) -> Dict:
        """
        单次 yt-dlp 调用同时下载视频和字幕（方案B）
        避免两次独立请求，节省等待时间，降低被检测概率
        返回下载结果字典
        """
        output_dir = os.path.join(self.cache_dir, video_id)
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        results = {
            'video': None,
            'subtitles': {},
            'info': None
        }

        self._wait_if_needed()

        # 根据配置构建 format 字符串
        video_quality = self.config.get('video_quality', 'bestvideo+bestaudio/best')
        if video_quality == '1080p':
            format_str = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'
        elif video_quality == '720p':
            format_str = 'bestvideo[height<=720]+bestaudio/best[height<=720]'
        elif video_quality == '480p':
            format_str = 'bestvideo[height<=480]+bestaudio/best[height<=480]'
        elif video_quality == 'best':
            format_str = 'bestvideo+bestaudio/best'
        else:
            format_str = video_quality

        download_subs = self.config.get('download_original_subtitles', True)

        ydl_opts = {
            'format': format_str,
            "ratelimit": 1_500_000,           # 约 1.5MB/s
            "sleep_interval": 20,
            "max_sleep_interval": 45,
            'outtmpl': os.path.join(output_dir, '%(id)s.%(ext)s'),
            'merge_output_format': 'mp4',
            # 同步下载英文字幕 - 优先人工字幕，fallback 自动 CC
            'writesubtitles': download_subs,
            'writeautomaticsub': download_subs,
            'subtitleslangs': ['en'],
            # json3 提供逐字时间戳，可重建无重叠句子段；srv3/vtt 作为降级备选
            'subtitlesformat': 'json3/srv3/vtt',
            # mweb: 无需 PO Token，无 SABR，支持 1080p；web 作为 fallback
            'extractor_args': {
                'youtube': {
                    'player_client': ['mweb', 'web'],
                }
            },
            # 启用 Node.js 运行时以解决 n-challenge（EJS）
            # Python API 格式：{runtime: {config}}，空字典表示使用默认配置
            'js_runtimes': {'node': {}},
            # 从 GitHub 自动下载 EJS challenge solver 脚本（首次运行时下载并缓存）
            'remote_components': ['ejs:github'],
            'noplaylist': True,           # 忽略 URL 中的 &list= 参数，只下载单个视频
            'retries': 10,
            'fragment_retries': 10,
            'file_access_retries': 5,
            'socket_timeout': 30,
            'quiet': False,
            'no_warnings': False,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            },
        }

        if self.config.get('proxy'):
            ydl_opts['proxy'] = self.config['proxy']
        self._apply_cookies(ydl_opts)

        for attempt in range(self.max_retries):
            try:
                logger.info(f"下载视频+字幕 (尝试 {attempt + 1}/{self.max_retries})...")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    dl_info = ydl.extract_info(url, download=True)

                actual_id = dl_info['id']

                # 填充视频元数据
                results['info'] = {
                    'id': dl_info.get('id'),
                    'title': dl_info.get('title'),
                    'duration': dl_info.get('duration'),
                    'description': dl_info.get('description'),
                    'uploader': dl_info.get('uploader'),
                    'uploader_id': dl_info.get('uploader_id'),   # e.g. '@SuperSimplePlay'
                    'upload_date': dl_info.get('upload_date'),
                }

                # 验证视频文件
                video_path = os.path.join(output_dir, f"{actual_id}.mp4")
                if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
                    results['video'] = video_path
                    logger.info(f"视频下载成功: {video_path}")
                else:
                    raise Exception("视频文件不存在或为空")

                # 检查字幕文件（优先 json3，fallback srv3/vtt）
                if download_subs:
                    subtitle_file = os.path.join(output_dir, f"{actual_id}.en.json3")
                    if not os.path.exists(subtitle_file):
                        subtitle_file = os.path.join(output_dir, f"{actual_id}.en.srv3")
                    if not os.path.exists(subtitle_file):
                        subtitle_file = os.path.join(output_dir, f"{actual_id}.en.vtt")
                    if os.path.exists(subtitle_file):
                        available_subs = dl_info.get('subtitles', {})
                        is_manual = 'en' in available_subs
                        sub_type = "人工字幕" if is_manual else "自动字幕(CC)"
                        ext = os.path.splitext(subtitle_file)[1]
                        results['subtitles']['en'] = subtitle_file
                        logger.info(f"✓ en {sub_type}下载成功 ({ext}): {subtitle_file}")
                    else:
                        logger.warning("✗ 未找到英文字幕（视频无字幕或字幕未生成）")
                        results['subtitles']['en'] = None

                return results

            except Exception as e:
                logger.error(f"下载失败 (尝试 {attempt + 1}): {str(e)}")
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.info(f"等待 {wait_time}s 后重试...")
                    time.sleep(wait_time)

        return results
