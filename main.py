"""
YouTube双语字幕生成系统 - 主程序
整合所有模块，提供完整的视频下载、转录、翻译、字幕合并和视频处理流程
"""

import os
import sys
import logging
from pathlib import Path
from typing import Dict, List

from core.utils import load_config, setup_logging, load_video_urls, sanitize_filename, get_output_path
from core.cache_manager import CacheManager
from core.downloader import VideoDownloader
from core.transcriber import WhisperTranscriber
from core.translator import LLMTranslator
from core.subtitle_merger import SubtitleMerger
from core.video_processor import VideoProcessor
from core.channel_scanner import scan_channels
from uploader.bilibili_uploader import BilibiliUploader


logger = logging.getLogger(__name__)


class SubtitleGenerator:
    """双语字幕生成器主类"""
    
    def __init__(self, config_path: str = "./config.yaml"):
        # 加载配置
        self.config = load_config(config_path)
        setup_logging(self.config)
        
        logger.info("="*50)
        logger.info("YouTube双语字幕生成系统启动")
        logger.info("="*50)
        
        # 初始化各个模块
        self.cache_manager = CacheManager(self.config['cache']['db_file'])
        self.downloader = VideoDownloader(self.config)
        self.transcriber = WhisperTranscriber(self.config)
        self.translator = LLMTranslator(self.config)
        self.subtitle_merger = SubtitleMerger(self.config)
        self.video_processor = VideoProcessor(self.config)

        # B站上传模块（可选，配置文件不存在时禁用）
        bili_cfg = './bili_upload.yaml'
        if os.path.exists(bili_cfg):
            self.bili_uploader = BilibiliUploader(bili_cfg)
        else:
            self.bili_uploader = None

        # 创建必要的目录
        self._setup_directories()
    
    def _setup_directories(self):
        """创建必要的目录"""
        for dir_path in [
            self.config['cache']['cache_dir'],
            self.config['cache']['output_dir'],
            Path(self.config['logging']['file']).parent
        ]:
            Path(dir_path).mkdir(parents=True, exist_ok=True)
    
    def process_video(self, video_entry: Dict) -> bool:
        """
        处理单个视频的完整流程
        """
        url = video_entry['url']
        video_type = video_entry.get('type', 'general')
        
        logger.info(f"\n{'='*50}")
        logger.info(f"开始处理视频: {url}")
        logger.info(f"视频类型: {video_type}")
        logger.info(f"{'='*50}\n")
        
        # 检查缓存（本地 URL 解析，不发网络请求）
        if self.cache_manager.is_processed(url):
            logger.info(f"视频已处理，跳过: {url}")
            return True
        
        try:
            # 1. 本地解析 video_id（无需额外网络请求）
            video_id = self.cache_manager.get_video_id(url)
            logger.info(f"视频ID: {video_id}")

            # 2. 单次请求：下载视频+字幕，同时获取元数据
            logger.info("开始下载视频和字幕...")
            download_results = self.downloader.download_all(url, video_id)

            if not download_results['video']:
                raise Exception("视频下载失败")

            # 从下载结果提取元数据（download_all 已内置 info 获取）
            video_info = download_results.get('info') or {}
            video_title = sanitize_filename(video_info.get('title') or video_id)

            logger.info(f"视频标题: {video_title}")

            # 翻译标题为中文供 B 站上传使用（快速小调用；失败时静默回退到原标题）
            title_zh = self.translator.translate_title(video_title, video_type)

            video_path = download_results['video']

            # ── 中文源视频快速路径 ──────────────────────────────────────────
            # type=zh：仅下载 + 复制到输出目录，跳过 Whisper / LLM / 字幕合并
            if video_type == 'zh':
                output_video_path = get_output_path(
                    video_id,
                    f"{video_title}.mp4",
                    self.config['cache']['output_dir'],
                    video_type
                )
                os.makedirs(os.path.dirname(output_video_path), exist_ok=True)
                if not self.video_processor.remux_to_aac(video_path, output_video_path):
                    raise Exception("中文源视频音频转码（Opus→AAC）失败")
                self.cache_manager.mark_processed(url, {
                    'video_id': video_id,
                    'title': video_title,
                    'title_zh': title_zh,
                    'type': video_type,
                    'output_video': output_video_path,
                })
                logger.info(f"\n{'='*50}")
                logger.info(f"中文源视频处理完成（仅下载）: {video_title}")
                logger.info(f"输出: {output_video_path}")
                logger.info(f"{'='*50}\n")
                return True
            # ───────────────────────────────────────────────────────────────

            # 3. 处理英文字幕（优先使用原始字幕，否则Whisper转录）
            logger.info("\n" + "="*50)
            logger.info("步骤3: 获取英文字幕")
            logger.info("="*50)
            
            en_srt_path = self._get_or_create_english_subtitle(
                video_id,
                video_path,
                download_results.get('subtitles', {}).get('en'),
                video_type
            )
            
            if not en_srt_path:
                raise Exception("无法获取英文字幕（原始字幕和Whisper转录均失败）")
            
            # 4. 生成中文翻译字幕
            zh_srt_path = self._translate_subtitle(video_id, en_srt_path, video_type)
            
            if not zh_srt_path:
                raise Exception("中文字幕翻译失败")
            
            # 5. 合并中英文字幕
            bilingual_srt_path = get_output_path(
                video_id, 
                f"{video_title}.bilingual.srt",
                self.config['cache']['output_dir'],
                video_type
            )
            
            if not self.subtitle_merger.merge_bilingual(en_srt_path, zh_srt_path, bilingual_srt_path):
                raise Exception("字幕合并失败")
            
            # 6. 嵌入字幕到视频（可选）
            output_video_path = None
            if self.config['video_processor']['embed_subtitles']:
                output_video_path = get_output_path(
                    video_id,
                    f"{video_title}.bilingual.mp4",
                    self.config['cache']['output_dir'],
                    video_type
                )
                
                if not self.video_processor.embed_subtitle(video_path, bilingual_srt_path, output_video_path):
                    logger.warning("字幕嵌入失败，但字幕文件已生成")
                    output_video_path = None

            # 7. 标记为已处理
            self.cache_manager.mark_processed(url, {
                'video_id': video_id,
                'title': video_title,
                'title_zh': title_zh,
                'type': video_type,
                'en_subtitle': en_srt_path,
                'zh_subtitle': zh_srt_path,
                'bilingual_subtitle': bilingual_srt_path,
                'output_video': output_video_path,   # 供 bilibili_uploader 使用
                # channel_id: 优先 videos.txt/channel_scanner 显式指定，fallback 到 yt-dlp 元数据
                'channel_id': video_entry.get('channel_id') or video_info.get('uploader_id', ''),
            })
            
            logger.info(f"\n{'='*50}")
            logger.info(f"视频处理完成: {video_title}")
            logger.info(f"输出目录: {Path(bilingual_srt_path).parent}")
            logger.info(f"{'='*50}\n")
            
            return True
            
        except Exception as e:
            logger.error(f"处理视频失败: {e}", exc_info=True)
            self.cache_manager.mark_failed(url, str(e))
            return False

    def embed_only(self, video_id: str) -> bool:
        """
        仅重新执行字幕嵌入步骤，跳过下载/转录/翻译。
        用于 debug 或修复嵌入失败的视频，不修改 cache 状态。
        从 cache 中读取已有的路径信息。
        """
        # 从 cache 查找该 video_id 的记录
        entry = next(
            (v for v in self.cache_manager.cache.values()
             if v.get('metadata', {}).get('video_id') == video_id),
            None
        )
        if not entry:
            logger.error(f"cache 中未找到 video_id={video_id}，请先完整处理一次")
            return False

        meta = entry['metadata']
        video_title = meta.get('title', video_id)
        video_type = meta.get('type', 'general')
        bilingual_srt = meta.get('bilingual_subtitle')
        cache_dir = self.config['cache']['cache_dir']
        output_dir = self.config['cache']['output_dir']

        video_path = os.path.join(cache_dir, video_id, f"{video_id}.mp4")
        if not os.path.exists(video_path):
            logger.error(f"视频文件不存在: {video_path}")
            return False
        if not bilingual_srt or not os.path.exists(bilingual_srt):
            logger.error(f"双语字幕文件不存在: {bilingual_srt}")
            return False

        output_video_path = get_output_path(
            video_id,
            f"{video_title}.bilingual.mp4",
            output_dir,
            video_type
        )

        logger.info(f"{'='*50}")
        logger.info(f"[embed-only] 视频: {video_title}")
        logger.info(f"  视频源: {video_path}")
        logger.info(f"  字幕源: {bilingual_srt}")
        logger.info(f"  输出:   {output_video_path}")
        logger.info(f"{'='*50}")

        success = self.video_processor.embed_subtitle(video_path, bilingual_srt, output_video_path)
        if success:
            logger.info(f"✓ embed-only 完成: {output_video_path}")
        else:
            logger.error(f"✗ embed-only 失败")
        return success
    
    def reprocess_subtitle(self, video_id: str) -> bool:
        """
        重新处理字幕（跳过视频下载），用于：
        - 修复 vtt_to_srt() 更新后的脏数据
        - 切换翻译模型后重新翻译
        - 修复字幕合并/嵌入问题
        流程: VTT -> SRT -> 翻译 -> 合并 -> 嵌入（可选）
        """
        entry = next(
            (v for v in self.cache_manager.cache.values()
             if v.get('metadata', {}).get('video_id') == video_id),
            None
        )
        if not entry:
            logger.error(f"cache 中未找到 video_id={video_id}，请先完整处理一次")
            return False

        meta = entry['metadata']
        video_title = meta.get('title', video_id)
        video_type = meta.get('type', 'general')
        cache_dir = Path(self.config['cache']['cache_dir']) / video_id
        output_dir = self.config['cache']['output_dir']

        logger.info(f"{'='*50}")
        logger.info(f"[reprocess-subtitle] 视频: {video_title}")
        logger.info(f"  video_id: {video_id}  type: {video_type}")
        logger.info(f"{'='*50}")

        # 步骤1：重新从原始字幕生成干净的英文 SRT
        json3_path = str(cache_dir / f"{video_id}.en.json3")
        srv3_path = str(cache_dir / f"{video_id}.en.srv3")
        vtt_path = str(cache_dir / f"{video_id}.en.vtt")
        en_srt_path = str(cache_dir / "subtitle.en.srt")

        if os.path.exists(json3_path):
            logger.info(f"json3 工业级分割流程: {json3_path}")
            if not self._json3_to_segmented_srt(json3_path, en_srt_path, video_type):
                logger.error("json3 -> SRT 转换失败")
                return False
            logger.info(f"  ✓ 英文 SRT: {en_srt_path}")
        elif os.path.exists(srv3_path):
            logger.info(f"重新转换 srv3 -> SRT: {srv3_path}")
            if not self.transcriber.srv3_to_srt(srv3_path, en_srt_path):
                logger.error("srv3 -> SRT 转换失败")
                return False
            logger.info(f"  ✓ 英文 SRT: {en_srt_path}")
        elif os.path.exists(vtt_path):
            logger.info(f"重新转换 VTT -> SRT（fallback）: {vtt_path}")
            if not self.transcriber.vtt_to_srt(vtt_path, en_srt_path):
                logger.error("VTT -> SRT 转换失败")
                return False
            logger.info(f"  ✓ 英文 SRT: {en_srt_path}")
        elif os.path.exists(en_srt_path):
            logger.info(f"  未找到原始字幕，使用现有 SRT: {en_srt_path}")
        else:
            logger.error(f"未找到字幕源文件（srv3/VTT/SRT）: {cache_dir}")
            return False

        # 步骤2：重新翻译
        logger.info(f"\n重新翻译字幕 (类型: {video_type})")
        zh_srt_path = self._translate_subtitle(video_id, en_srt_path, video_type)
        if not zh_srt_path:
            logger.error("中文字幕翻译失败")
            return False
        logger.info(f"  ✓ 中文 SRT: {zh_srt_path}")

        # 步骤3：重新合并
        logger.info("\n重新合并双语字幕")
        bilingual_srt_path = get_output_path(
            video_id,
            f"{video_title}.bilingual.srt",
            output_dir,
            video_type
        )
        if not self.subtitle_merger.merge_bilingual(en_srt_path, zh_srt_path, bilingual_srt_path):
            logger.error("字幕合并失败")
            return False
        logger.info(f"  ✓ 双语 SRT: {bilingual_srt_path}")

        # 步骤4：重新嵌入（可选）
        if self.config['video_processor']['embed_subtitles']:
            video_path = str(cache_dir / f"{video_id}.mp4")
            if not os.path.exists(video_path):
                logger.warning(f"视频文件不存在，跳过嵌入: {video_path}")
            else:
                logger.info("\n重新嵌入字幕到视频")
                output_video_path = get_output_path(
                    video_id,
                    f"{video_title}.bilingual.mp4",
                    output_dir,
                    video_type
                )
                if not self.video_processor.embed_subtitle(video_path, bilingual_srt_path, output_video_path):
                    logger.warning("字幕嵌入失败，但字幕文件已生成")
                else:
                    logger.info(f"  ✓ 输出视频: {output_video_path}")

        # 更新 cache 元数据
        meta['en_subtitle'] = en_srt_path
        meta['zh_subtitle'] = zh_srt_path
        meta['bilingual_subtitle'] = bilingual_srt_path
        self.cache_manager._save_cache()

        logger.info(f"\n{'='*50}")
        logger.info(f"[reprocess-subtitle] 完成: {video_title}")
        logger.info(f"{'='*50}\n")
        return True

    def _json3_to_segmented_srt(self, json3_path: str, en_srt_path: str,
                                 video_type: str = 'general') -> bool:
        """
        json3 → SRT 入口。
          - caption_segmentation.enabled=false（默认）：直接调用 json3_to_srt()（停顿+标点分句）
          - caption_segmentation.enabled=true：LLM 语义分割 + 展示规则，失败时回退到分句算法
        """
        seg_cfg = self.config.get('translator', {}).get('caption_segmentation', {})

        if not seg_cfg.get('enabled', False):
            logger.info("  使用停顿+标点分句算法（caption_segmentation.enabled=false）")
            return self.transcriber.json3_to_srt(json3_path, en_srt_path)

        # ── Deterministic segmentation + LLM soft advisory ───────────────
        # Hard segmentation decisions are made entirely by symbolic rules
        # (SemanticBreakScorer + pause detection + punctuation).
        # LLM is consulted only for ambiguous boundaries (score 0.30–0.65)
        # and its vote is weighted at 0.25 — it can tip the balance but
        # cannot override a hard block or a strong break.
        max_chars = seg_cfg.get('max_chars_per_line', 70)
        max_lines = seg_cfg.get('max_lines', 1)
        max_cps   = seg_cfg.get('max_cps', 20)
        min_dur   = seg_cfg.get('min_dur_ms', 833)
        max_dur   = seg_cfg.get('max_dur_ms', 7000)

        logger.info("  [1/4] 提取词级时间流...")
        words = self.transcriber.json3_extract_words(json3_path)
        if not words:
            logger.warning("  词流提取失败，回退到直接转换")
            return self.transcriber.json3_to_srt(json3_path, en_srt_path)

        logger.info(f"  [2/4] 确定性断点评分（{len(words)} 词）...")
        boundary_scores = self.transcriber.compute_boundary_scores(words)

        # Collect ambiguous zone: [0.30, 0.65] — where LLM vote can change outcome
        # (below 0.30 = deterministic "don't break"; above 0.65 = deterministic "break")
        AMBIG_LO, AMBIG_HI = 0.30, 0.65
        ambiguous = [
            i for i, s in enumerate(boundary_scores)
            if AMBIG_LO <= s <= AMBIG_HI
        ]

        llm_votes: dict = {}
        if ambiguous:
            logger.info(f"  [3/4] LLM 软建议（{len(ambiguous)} 个模糊断点 / {len(boundary_scores)} 总断点）...")
            try:
                llm_votes = self.translator.validate_breaks_llm(words, ambiguous)
            except Exception as e:
                logger.warning(f"  LLM 软建议失败（{e}），继续使用纯确定性分组")
        else:
            logger.info("  [3/4] 无模糊断点，跳过 LLM 咨询")

        logger.info(f"  [4/4] 最终分组 + 展示规则 → SRT...")
        groups = self.transcriber.build_groups_from_scores(
            words, boundary_scores,
            max_chars=max_chars, max_dur_ms=max_dur,
            llm_votes=llm_votes,
        )
        if not groups:
            logger.warning("  分组结果为空，回退到直接转换")
            return self.transcriber.json3_to_srt(json3_path, en_srt_path)

        ok = self.transcriber.words_to_srt(
            words, groups, en_srt_path,
            max_cps=max_cps, max_chars_per_line=max_chars,
            max_lines=max_lines, min_dur_ms=min_dur, max_dur_ms=max_dur,
        )
        if not ok:
            logger.warning("  展示规则处理失败，回退到直接转换")
            return self.transcriber.json3_to_srt(json3_path, en_srt_path)

        return True

    def _get_or_create_english_subtitle(self, video_id: str, video_path: str,
                                        downloaded_subtitle: str = None,
                                        video_type: str = 'general') -> str:
        """
        获取或创建英文字幕
        策略：
        1. 优先使用YouTube原始字幕（包括自动生成的CC字幕）
        2. 如果没有原始字幕，使用Whisper转录
        """
        cache_dir = Path(self.config['cache']['cache_dir']) / video_id
        en_srt_path = str(cache_dir / "subtitle.en.srt")
        
        # 策略1：尝试使用下载的原始字幕
        if downloaded_subtitle and os.path.exists(downloaded_subtitle):
            try:
                file_size = os.path.getsize(downloaded_subtitle)
                logger.info(f"✓ 发现YouTube原始英文字幕（{'自动生成' if 'auto' in downloaded_subtitle else '人工'}）")
                logger.info(f"  字幕文件: {downloaded_subtitle} ({file_size} bytes)")
                logger.info(f"  跳过Whisper转录，节省时间和GPU资源")
                
                if downloaded_subtitle.endswith('.json3'):
                    logger.info(f"  json3 工业级分割流程（语义分割 + 展示规则）...")
                    if self._json3_to_segmented_srt(downloaded_subtitle, en_srt_path, video_type):
                        logger.info(f"  ✓ 转换成功: {en_srt_path}")
                        return en_srt_path
                    else:
                        logger.warning(f"  json3转换失败，将使用Whisper转录")
                elif downloaded_subtitle.endswith('.srv3'):
                    logger.info(f"  转换srv3(XML) -> SRT格式（原始离散段落，无重叠）...")
                    if self.transcriber.srv3_to_srt(downloaded_subtitle, en_srt_path):
                        logger.info(f"  ✓ 转换成功: {en_srt_path}")
                        return en_srt_path
                    else:
                        logger.warning(f"  srv3转换失败，将使用Whisper转录")
                elif downloaded_subtitle.endswith('.vtt'):
                    logger.info(f"  转换VTT -> SRT格式（含滚动窗口去重）...")
                    if self.transcriber.vtt_to_srt(downloaded_subtitle, en_srt_path):
                        logger.info(f"  ✓ 转换成功: {en_srt_path}")
                        return en_srt_path
                    else:
                        logger.warning(f"  VTT转换失败，将使用Whisper转录")
                else:
                    # 如果已经是SRT，直接复制
                    import shutil
                    shutil.copy(downloaded_subtitle, en_srt_path)
                    logger.info(f"  ✓ 字幕已就绪: {en_srt_path}")
                    return en_srt_path
            except Exception as e:
                logger.warning(f"  处理下载字幕失败: {e}，将使用Whisper转录")
        else:
            logger.info("✗ 未找到YouTube原始字幕")
        
        # 策略2：使用Whisper转录
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("使用Whisper ASR生成英文字幕")
        logger.info(f"  模型: {self.config['transcriber']['model']}")
        logger.info(f"  设备: {self.config['transcriber']['device']}")
        logger.info("  这可能需要几分钟时间，请耐心等待...")
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        if self.transcriber.transcribe_and_save(video_path, en_srt_path):
            logger.info(f"✓ Whisper转录完成: {en_srt_path}")
            return en_srt_path
        else:
            logger.error("✗ Whisper转录失败")
            return None
    
    def _translate_subtitle(self, video_id: str, en_srt_path: str, video_type: str) -> str:
        """
        翻译英文字幕为中文
        """
        cache_dir = Path(self.config['cache']['cache_dir']) / video_id
        zh_srt_path = str(cache_dir / "subtitle.zh.srt")
        
        logger.info(f"开始翻译字幕 (类型: {video_type})")
        
        if self.translator.translate_srt(en_srt_path, zh_srt_path, video_type):
            return zh_srt_path
        
        return None
    
    def process_all(self, videos_file: str = "./videos.txt",
                    channels_file: str = "./channels.yaml"):
        """
        处理视频列表中的所有视频。
        启动时先对上次遗留的未归档视频补充系列，再扫描频道、处理新视频。
        """
        # ── [-1] 自动补充系列：对上次已上传但 series_fixed!=True 的视频 ────
        # 距上次运行通常已过数小时，B站自动审核（30分钟内）基本完成
        if self.bili_uploader is not None:
            self.bili_uploader.fix_series(self.cache_manager)
        # ────────────────────────────────────────────────────────────

        # ── [0] 频道扫描：发现新视频并前置入队 ───────────────────────────
        logger.info("\n" + "="*50)
        logger.info("扫描订阅频道（channels.yaml）...")
        channel_entries = scan_channels(self.config, channels_file)
        # ────────────────────────────────────────────────────────────

        video_list = load_video_urls(videos_file)
        
        if not video_list and not channel_entries:
            logger.warning(f"未找到视频列表或列表为空: {videos_file}")
            return

        # 频道扫描到的新视频放在队列头部（优先处理新内容）
        combined = channel_entries + video_list
        logger.info(f"共找到 {len(combined)} 个视频待处理（频道新增 {len(channel_entries)} + videos.txt {len(video_list)}）")
        
        # 显示缓存统计
        stats = self.cache_manager.get_statistics()
        logger.info(f"缓存统计: 总计 {stats['total']}, 成功 {stats['successful']}, 失败 {stats['failed']}")
        
        # 处理每个视频
        success_count = 0
        skip_count = 0
        fail_count = 0
        
        for i, video_entry in enumerate(combined, 1):
            logger.info(f"\n进度: {i}/{len(combined)}")
            
            # 检查是否已处理
            if self.cache_manager.is_processed(video_entry['url']):
                skip_count += 1
                continue
            
            # 处理视频
            if self.process_video(video_entry):
                success_count += 1
            else:
                fail_count += 1
        
        # 显示最终统计
        logger.info(f"\n{'='*50}")
        logger.info("处理完成统计:")
        logger.info(f"  成功: {success_count}")
        logger.info(f"  跳过: {skip_count}")
        logger.info(f"  失败: {fail_count}")
        logger.info(f"  总计: {len(combined)}")
        logger.info(f"{'='*50}\n")

        # ── [2] B站上传：处理完成后自动上传待上传视频 ──────────────────
        if self.bili_uploader is not None:
            logger.info("\n" + "="*50)
            logger.info("开始B站上传...")
            self.bili_uploader.upload_pending(
                self.cache_manager,
                output_dir=self.config['cache']['output_dir']
            )
        # ────────────────────────────────────────────────────────────


def main():
    """主函数"""
    # 解析命令行参数
    import argparse
    
    parser = argparse.ArgumentParser(description='YouTube双语字幕生成系统')
    parser.add_argument('--config', default='./config.yaml', help='配置文件路径')
    parser.add_argument('--channels', default='./channels.yaml', help='频道订阅列表文件路径')
    parser.add_argument('--videos', default='./videos.txt', help='视频列表文件路径')
    parser.add_argument('--url', help='处理单个视频URL')
    parser.add_argument('--type', default='general',
                       help='视频类型（对应 config.yaml 中 video_types 的键，如 baby/tech/interview/documentary/general，可自由扩展）')
    parser.add_argument('--embed-only', metavar='VIDEO_ID',
                       help='仅重新嵌入字幕，跳过下载/转录/翻译（传入 video_id，如 VnxyEGCIi2Y）')
    parser.add_argument('--reprocess-subtitle', metavar='VIDEO_ID',
                       help='重新处理字幕（VTT->SRT->翻译->合并->嵌入），跳过视频下载（传入 video_id）')
    parser.add_argument('--bili-login', action='store_true',
                       help='扫码登录B站，生成凭证文件（首次使用必须执行）')
    parser.add_argument('--upload-only', action='store_true',
                       help='跳过视频下载/处理，仅对已处理但未上传的视频执行B站上传')
    parser.add_argument('--mark-uploaded', metavar='VIDEO_ID',
                       help='将指定 video_id 标记为已手动上传B站（跳过自动上传），可配合 --bvid 记录BV号')
    parser.add_argument('--bvid', default='',
                       help='配合 --mark-uploaded 使用，记录B站 BV 号（可选）')
    parser.add_argument('--fix-series', action='store_true',
                       help='对所有已上传（upload_status=uploaded）但 series_fixed 未标记的视频，补充添加到B站系列（视频过审后运行）')

    args = parser.parse_args()
    
    try:
        generator = SubtitleGenerator(args.config)
        
        if args.embed_only:
            generator.embed_only(args.embed_only)
        elif args.reprocess_subtitle:
            generator.reprocess_subtitle(args.reprocess_subtitle)
        elif args.bili_login:
            if generator.bili_uploader is None:
                logger.error("bili_upload.yaml 不存在，无法执行B站登录")
                sys.exit(1)
            generator.bili_uploader.login_qrcode()
        elif args.upload_only:
            if generator.bili_uploader is None:
                logger.error("bili_upload.yaml 不存在，无法执行B站上传")
                sys.exit(1)
            generator.bili_uploader.upload_pending(
                generator.cache_manager,
                output_dir=generator.config['cache']['output_dir']
            )
        elif args.mark_uploaded:
            # 将指定 video_id 标记为已手动上传（跳过自动上传队列）
            video_id = args.mark_uploaded
            entry = generator.cache_manager.cache.get(video_id)
            if not entry:
                logger.error(f"cache 中未找到 video_id={video_id}")
                sys.exit(1)
            entry.setdefault('metadata', {}).update({
                'upload_status': 'uploaded',
                'bvid': args.bvid or 'manual',
                'uploaded_at': __import__('datetime').datetime.now().isoformat(),
            })
            generator.cache_manager._save_cache()
            bvid_info = f" BV号: {args.bvid}" if args.bvid else ""
            logger.info(f"✓ 已标记为手动上传: {video_id}{bvid_info}")
        elif args.fix_series:
            if generator.bili_uploader is None:
                logger.error("bili_upload.yaml 不存在，无法执行系列修复")
                sys.exit(1)
            generator.bili_uploader.fix_series(generator.cache_manager)
        elif args.url:
            # 处理单个视频
            video_entry = {
                'url': args.url,
                'type': args.type,
                'note': 'Command line input'
            }
            generator.process_video(video_entry)
        else:
            # 处理视频列表
            generator.process_all(args.videos, args.channels)
            
    except KeyboardInterrupt:
        logger.info("\n用户中断，程序退出")
        sys.exit(0)
    except Exception as e:
        logger.error(f"程序异常: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
