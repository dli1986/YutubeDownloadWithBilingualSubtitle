"""
工具函数模块
提供通用的辅助功能
"""

import os
import logging
from pathlib import Path
from typing import List, Optional, Dict
import yaml
import re


def load_config(config_path: str = "./config.yaml") -> Dict:
    """加载配置文件"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def setup_logging(config: Dict):
    """设置日志系统"""
    log_config = config.get('logging', {})
    log_file = log_config.get('file', './logs/subtitle_generator.log')
    
    # 创建日志目录
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=getattr(logging, log_config.get('level', 'INFO')),
        format=log_config.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s'),
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )


def load_video_urls(file_path: str = "./videos.txt") -> List[Dict[str, str]]:
    """
    从文件加载视频URL列表
    返回格式：[{"url": "...", "type": "baby", "note": "..."}, ...]
    """
    videos = []
    if not os.path.exists(file_path):
        logging.warning(f"视频列表文件不存在: {file_path}")
        return videos
    
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # 跳过空行和注释
            if not line or line.startswith('#'):
                continue
            
            # 解析行内容
            # 格式: URL [type] [@channel] [note]
            # @channel 可选，若 parts[2] 以 @ 开头则作为 channel_id
            parts = line.split(None, 3)  # 最多分割成4部分
            if not parts:
                continue

            video_type = parts[1] if len(parts) > 1 else 'general'
            rest = parts[2] if len(parts) > 2 else ''
            note = parts[3] if len(parts) > 3 else ''
            if rest.startswith('@'):
                channel_id = rest
            else:
                channel_id = ''
                note = (rest + ' ' + note).strip()

            video_entry = {
                'url': parts[0],
                'type': video_type,
                'channel_id': channel_id,
                'note': note,
            }
            videos.append(video_entry)
    
    return videos


def sanitize_filename(filename: str) -> str:
    """清理文件名，移除非法字符"""
    # 移除Windows文件名中的非法字符
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # 限制长度
    if len(filename) > 200:
        filename = filename[:200]
    return filename


def format_time(seconds: float) -> str:
    """将秒数格式化为可读时间"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"


def ensure_dir(path: str):
    """确保目录存在"""
    Path(path).mkdir(parents=True, exist_ok=True)


def get_output_path(video_id: str, filename: str, output_dir: str = "./output",
                    video_type: str = None) -> str:
    """生成输出文件路径，结构：output_dir/[video_type/]video_id/filename"""
    if video_type:
        video_dir = Path(output_dir) / video_type / video_id
    else:
        video_dir = Path(output_dir) / video_id
    ensure_dir(str(video_dir))
    return str(video_dir / filename)


def merge_srt_times(start1, end1, start2, end2):
    """
    合并两个字幕的时间轴，返回重叠范围
    """
    # 计算重叠的开始和结束时间
    overlap_start = max(start1.ordinal, start2.ordinal)
    overlap_end = min(end1.ordinal, end2.ordinal)
    
    # 检查是否有重叠
    if overlap_start < overlap_end:
        return True, overlap_start, overlap_end
    return False, None, None
