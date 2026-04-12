"""
视频处理模块
使用ffmpeg处理视频，包括嵌入字幕、重封装等
"""

import os
import re
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Dict, Optional


logger = logging.getLogger(__name__)


class VideoProcessor:
    """视频处理器"""
    
    def __init__(self, config: Dict):
        self.config = config.get('video_processor', {})
        self.embed_subtitles = self.config.get('embed_subtitles', True)
        self.video_codec = self.config.get('video_codec', 'h264')
        self.keep_original = self.config.get('keep_original', True)
        self.subtitle_font_size = self.config.get('subtitle_font_size', 16)
    
    def check_ffmpeg(self) -> bool:
        """检查ffmpeg是否可用"""
        try:
            result = subprocess.run(
                ['ffmpeg', '-version'],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except FileNotFoundError:
            logger.error("未找到ffmpeg，请确保已安装ffmpeg并添加到PATH")
            return False
    
    def _build_encode_args(self) -> list:
        """
        根据配置选择编码器。
        优先使用 GPU 硬件编码（h264_nvenc），不可用时自动回退到 CPU（libx264）。
        """
        use_gpu = self.config.get('use_gpu_encode', True)
        if use_gpu:
            # NVENC：GPU 专用编码单元，速度比 libx264 快 5-10x，不占 CUDA 核心
            # -rc vbr / -cq 23 等价于 libx264 的 -crf 23
            return ['-c:v', 'h264_nvenc', '-preset', 'p4', '-rc', 'vbr', '-cq', '23']
        else:
            return ['-c:v', 'libx264', '-preset', 'medium', '-crf', '23']

    def embed_subtitle(self, video_path: str, subtitle_path: str, output_path: str) -> bool:
        """
        将字幕嵌入视频（硬字幕）
        使用subtitles滤镜，确保SRT文件格式正确
        """
        if not self.check_ffmpeg():
            return False
        
        try:
            # 清理和验证字幕文件
            cleaned_subtitle = self._clean_subtitle_file(subtitle_path)
            if not cleaned_subtitle:
                logger.error("字幕文件清理失败")
                return False
            
            # 所有路径统一转为绝对路径，避免 Windows 相对路径问题
            video_abs = os.path.abspath(video_path)
            output_abs = os.path.abspath(output_path)
            output_dir = os.path.dirname(output_abs)
            video_id = Path(video_abs).stem  # VnxyEGCIi2Y.mp4 → VnxyEGCIi2Y

            # 字幕和输出都用 video_id 做临时文件名（纯字母数字，无特殊字符）
            temp_subtitle = os.path.join(output_dir, f"{video_id}.tmp.srt")
            temp_output   = os.path.join(output_dir, f"{video_id}.tmp.mp4")
            shutil.copy2(os.path.abspath(cleaned_subtitle), temp_subtitle)

            # Windows 盘符含 ':' 导致 libass filter 解析失败，无论怎么转义都不可靠。
            # 解决方法：设置 cwd 为字幕所在目录，filter 中只传文件名（无路径无冒号）。
            subtitle_filename = os.path.basename(temp_subtitle)  # VnxyEGCIi2Y.tmp.srt

            encode_args = self._build_encode_args()
            cmd = [
                'ffmpeg',
                '-i', video_abs,
                '-vf', f"subtitles='{subtitle_filename}':force_style='FontSize={self.subtitle_font_size},PrimaryColour=&Hffffff,MarginV=10,Alignment=2'",
                *encode_args,
                '-c:a', 'aac',   # 转码为 AAC，安全兼容主流播放器（原始 Opus Windows 默认播放器不支持）
                '-b:a', '192k',  # 音频码率
                '-y',
                temp_output
            ]

            logger.info(f"开始嵌入字幕到视频...")
            logger.info(f"  字幕临时文件: {temp_subtitle}")
            logger.info(f"  视频临时输出: {temp_output}")

            # 用 Popen 流式读取 stderr，实时解析 ffmpeg 进度
            process = subprocess.Popen(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                text=True,
                cwd=output_dir
            )

            duration_sec = 0.0
            stderr_lines = []
            for line in process.stderr:
                stderr_lines.append(line)
                line = line.strip()
                # 从 Duration 行获取总时长
                if duration_sec == 0 and line.startswith('Duration:'):
                    m = re.search(r'Duration:\s*(\d+):(\d+):([\d.]+)', line)
                    if m:
                        duration_sec = int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))
                # 从 frame= 行获取当前进度
                if line.startswith('frame=') or 'time=' in line:
                    m = re.search(r'time=(\d+):(\d+):([\d.]+)', line)
                    if m and duration_sec > 0:
                        current = int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))
                        pct = min(current / duration_sec * 100, 100)
                        bar_len = 30
                        filled = int(bar_len * pct / 100)
                        bar = '█' * filled + '░' * (bar_len - filled)
                        elapsed_str = f"{int(current//60):02d}:{int(current%60):02d}"
                        total_str   = f"{int(duration_sec//60):02d}:{int(duration_sec%60):02d}"
                        print(f"\r  [{bar}] {pct:5.1f}%  {elapsed_str}/{total_str}", end='', flush=True)

            process.wait()
            print()  # 进度条换行

            if process.returncode == 0:
                # ffmpeg 成功后 rename 到最终路径（含标题的文件名）
                os.replace(temp_output, output_abs)
                logger.info(f"✓ 字幕嵌入成功: {output_abs}")
                # 清理所有临时文件
                for tmp in [temp_subtitle]:
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
                if cleaned_subtitle != subtitle_path:
                    try:
                        os.remove(cleaned_subtitle)
                    except Exception:
                        pass
                return True
            else:
                # 失败时清理临时文件并输出错误
                for tmp in [temp_subtitle, temp_output]:
                    try:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    except Exception:
                        pass
                logger.error(f"字幕嵌入失败:\n{''.join(stderr_lines[-20:])}")  # 只打印最后20行
                return False
                
        except Exception as e:
            logger.error(f"嵌入字幕时出错: {e}")
            return False
    
    def _clean_subtitle_file(self, subtitle_path: str) -> Optional[str]:
        """
        清理字幕文件，移除可能导致显示问题的格式标记
        返回清理后的临时文件路径
        """
        try:
            import pysrt
            
            subs = pysrt.open(subtitle_path, encoding='utf-8')
            cleaned_subs = pysrt.SubRipFile()
            
            for sub in subs:
                # 清理文本：移除HTML标签和特殊格式
                text = sub.text
                # 移除时间戳标记（如 <00:21:06.559>）
                import re
                text = re.sub(r'<\d{2}:\d{2}:\d{2}\.\d{3}>', '', text)
                # 移除其他HTML标签
                text = re.sub(r'<[^>]+>', '', text)
                # 移除多余空格和换行
                text = '\n'.join([line.strip() for line in text.split('\n') if line.strip()])
                
                if text:  # 只保留非空字幕
                    cleaned_sub = pysrt.SubRipItem(
                        index=len(cleaned_subs) + 1,
                        start=sub.start,
                        end=sub.end,
                        text=text
                    )
                    cleaned_subs.append(cleaned_sub)
            
            # 保存清理后的文件
            cleaned_path = subtitle_path.replace('.srt', '.cleaned.srt')
            cleaned_subs.save(cleaned_path, encoding='utf-8')
            logger.info(f"✓ 字幕文件已清理: {len(cleaned_subs)} 条有效字幕")
            
            return cleaned_path
            
        except Exception as e:
            logger.error(f"清理字幕文件失败: {e}")
            return subtitle_path  # 返回原文件
    
    def attach_subtitle(self, video_path: str, subtitle_path: str, output_path: str, 
                       subtitle_lang: str = 'chi') -> bool:
        """
        将字幕作为软字幕附加到视频
        """
        if not self.check_ffmpeg():
            return False
        
        try:
            cmd = [
                'ffmpeg',
                '-i', video_path,
                '-i', subtitle_path,
                '-c', 'copy',
                '-c:s', 'mov_text',  # 字幕编码
                '-metadata:s:s:0', f'language={subtitle_lang}',
                '-y',
                output_path
            ]
            
            logger.info(f"开始附加字幕: {video_path}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                logger.info(f"字幕附加成功: {output_path}")
                return True
            else:
                logger.error(f"字幕附加失败: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"附加字幕时出错: {e}")
            return False
    
    def remux_video(self, input_path: str, output_path: str) -> bool:
        """
        重新封装视频（不重新编码）
        用于修复某些视频的兼容性问题
        """
        if not self.check_ffmpeg():
            return False
        
        try:
            cmd = [
                'ffmpeg',
                '-i', input_path,
                '-c', 'copy',
                '-y',
                output_path
            ]
            
            logger.info(f"开始重封装视频: {input_path}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                logger.info(f"视频重封装成功: {output_path}")
                return True
            else:
                logger.error(f"视频重封装失败: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"重封装视频时出错: {e}")
            return False
    
    def extract_audio(self, video_path: str, audio_path: str) -> bool:
        """
        从视频提取音频
        """
        if not self.check_ffmpeg():
            return False
        
        try:
            cmd = [
                'ffmpeg',
                '-i', video_path,
                '-vn',  # 不包含视频
                '-acodec', 'pcm_s16le',  # 转换为WAV格式
                '-ar', '16000',  # 采样率16kHz
                '-ac', '1',  # 单声道
                '-y',
                audio_path
            ]
            
            logger.info(f"开始提取音频: {video_path}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                logger.info(f"音频提取成功: {audio_path}")
                return True
            else:
                logger.error(f"音频提取失败: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"提取音频时出错: {e}")
            return False
    
    def get_video_info(self, video_path: str) -> Optional[Dict]:
        """
        获取视频信息
        """
        if not self.check_ffmpeg():
            return None
        
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                video_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                import json
                return json.loads(result.stdout)
            else:
                return None
                
        except Exception as e:
            logger.error(f"获取视频信息失败: {e}")
            return None
