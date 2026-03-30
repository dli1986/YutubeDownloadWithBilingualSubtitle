"""
Whisper转录模块
使用OpenAI Whisper进行音频转录，生成英文字幕
"""

import os
import re
import html
import logging
from pathlib import Path
from typing import Dict, Optional
import whisper
import pysrt
from datetime import timedelta


logger = logging.getLogger(__name__)


class WhisperTranscriber:
    """Whisper ASR转录器"""
    
    def __init__(self, config: Dict):
        self.config = config.get('transcriber', {})
        self.model = None
        self.model_name = self.config.get('model', 'medium')
        self.device = self.config.get('device', 'cpu')
        self.language = self.config.get('language', 'en')
        
        logger.info(f"初始化Whisper模型: {self.model_name} (设备: {self.device})")
    
    def load_model(self):
        """加载Whisper模型"""
        if self.model is None:
            try:
                self.model = whisper.load_model(
                    self.model_name,
                    device=self.device
                )
                logger.info(f"Whisper模型加载成功: {self.model_name}")
            except Exception as e:
                logger.error(f"Whisper模型加载失败: {e}")
                raise
    
    def transcribe_video(self, video_path: str) -> Optional[Dict]:
        """
        转录视频音频
        返回Whisper的原始结果
        """
        if not os.path.exists(video_path):
            logger.error(f"视频文件不存在: {video_path}")
            return None
        
        self.load_model()
        
        try:
            logger.info(f"开始转录视频: {video_path}")
            result = self.model.transcribe(
                video_path,
                language=self.language,
                task=self.config.get('task', 'transcribe'),
                fp16=self.config.get('fp16', False),
                verbose=True
            )
            logger.info(f"转录完成，共 {len(result.get('segments', []))} 个片段")
            return result
        except Exception as e:
            logger.error(f"转录失败: {e}")
            return None
    
    def result_to_srt(self, result: Dict, output_path: str) -> bool:
        """
        将Whisper结果转换为SRT字幕文件
        """
        try:
            subs = pysrt.SubRipFile()
            
            for i, segment in enumerate(result['segments'], start=1):
                start_time = self._seconds_to_time(segment['start'])
                end_time = self._seconds_to_time(segment['end'])
                text = segment['text'].strip()
                
                sub = pysrt.SubRipItem(
                    index=i,
                    start=start_time,
                    end=end_time,
                    text=text
                )
                subs.append(sub)
            
            subs.save(output_path, encoding='utf-8')
            logger.info(f"SRT字幕保存成功: {output_path}")
            return True
        except Exception as e:
            logger.error(f"SRT保存失败: {e}")
            return False
    
    def _seconds_to_time(self, seconds: float) -> pysrt.SubRipTime:
        """将秒数转换为SubRipTime对象"""
        td = timedelta(seconds=seconds)
        hours = td.seconds // 3600
        minutes = (td.seconds % 3600) // 60
        secs = td.seconds % 60
        millis = td.microseconds // 1000
        
        return pysrt.SubRipTime(hours=hours, minutes=minutes, seconds=secs, milliseconds=millis)
    
    def transcribe_and_save(self, video_path: str, output_path: str) -> bool:
        """
        转录视频并直接保存为SRT文件
        """
        result = self.transcribe_video(video_path)
        if result:
            return self.result_to_srt(result, output_path)
        return False
    
    def vtt_to_srt(self, vtt_path: str, srt_path: str) -> bool:
        """
        将VTT字幕转换为SRT格式，并清理：
        - HTML 实体（&gt; &amp; 等）
        - VTT 内联时间标签（<00:00:04.240><c>text</c>）
        - 多余的 >> 前缀和重复行
        """
        try:
            with open(vtt_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 手动解析 VTT: 提取时间段和正文
            blocks = re.split(r'\n{2,}', content.strip())

            def _vtt_time_to_ms(t: str) -> int:
                """将 HH:MM:SS.mmm 或MM:SS.mmm 转换为毫秒"""
                t = t.strip()
                parts = t.replace(',', '.').split(':')
                if len(parts) == 3:
                    h, m, s = parts
                elif len(parts) == 2:
                    h, m, s = '0', parts[0], parts[1]
                else:
                    return 0
                return int(h) * 3600000 + int(m) * 60000 + int(float(s) * 1000)

            # 第一遍：收集所有条目 (start_ms, end_ms, time_line_srt, text)
            raw_entries = []
            for block in blocks:
                lines = block.strip().splitlines()
                if not lines:
                    continue
                time_line_idx = next(
                    (i for i, l in enumerate(lines) if '-->' in l), None
                )
                if time_line_idx is None:
                    continue
                raw_time = lines[time_line_idx]
                raw_time = raw_time.split(' align:')[0].split(' line:')[0].split(' position:')[0]

                time_parts = raw_time.split('-->')
                start_ms = _vtt_time_to_ms(time_parts[0])
                end_ms   = _vtt_time_to_ms(time_parts[1])
                time_line_srt = raw_time.replace('.', ',')

                text_lines = lines[time_line_idx + 1:]
                if not text_lines:
                    continue

                text = ' '.join(text_lines)
                text = re.sub(r'<\d{2}:\d{2}[:.\d]*>', '', text)
                text = re.sub(r'</?c>', '', text)
                text = re.sub(r'<v\b[^>]*>', '', text)
                text = re.sub(r'</v>', '', text)
                text = re.sub(r'<rt>[^<]*</rt>', '', text)
                text = re.sub(r'<[^>]+>', '', text)
                text = html.unescape(text)
                text = re.sub(r'^\s*>>\s*', '', text)
                text = re.sub(r'\s*>>\s*', ' ', text)
                text = re.sub(r' {2,}', ' ', text).strip()

                if not text:
                    continue

                raw_entries.append((start_ms, end_ms, time_line_srt, text))

            # 第二遍：去重
            # 1) 过滤 YouTube 滚动窗口的过渡帧（duration < 200ms）
            entries = [
                e for e in raw_entries if (e[1] - e[0]) >= 200
            ]
            # 2) 合并相邻的完全相同文本（保留第一个的 start、最后一个的 end）
            merged = []
            for entry in entries:
                if merged and merged[-1][3] == entry[3]:
                    prev = merged[-1]
                    merged[-1] = (prev[0], entry[1],
                                  f"{prev[2].split(' --> ')[0]} --> {entry[2].split(' --> ')[1]}",
                                  entry[3])
                else:
                    merged.append(list(entry))

            # 第三遍：写入 SRT
            srt_entries = []
            for idx, (_, _, time_line_srt, text) in enumerate(merged, 1):
                srt_entries.append(f"{idx}\n{time_line_srt}\n{text}\n")

            with open(srt_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(srt_entries))

            logger.info(f"VTT转SRT成功: {srt_path}（{len(merged)} 条）")
            return True
        except Exception as e:
            logger.error(f"VTT转SRT失败: {e}")
            return False
