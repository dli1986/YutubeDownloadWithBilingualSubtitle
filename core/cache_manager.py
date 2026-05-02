"""
缓存管理模块
用于跟踪已处理的视频，避免重复下载和处理
"""

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List
import hashlib


class CacheManager:
    """管理已处理视频的缓存"""
    
    def __init__(self, db_file: str = "./cache/processed_videos.json"):
        self.db_file = Path(db_file)
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache: Dict = self._load_cache()
    
    def _load_cache(self) -> Dict:
        """加载缓存数据库"""
        if self.db_file.exists():
            try:
                with open(self.db_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载缓存失败: {e}，创建新缓存")
                return {}
        return {}
    
    def _save_cache(self):
        """保存缓存到文件"""
        try:
            with open(self.db_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存缓存失败: {e}")
    
    def get_video_id(self, url: str) -> str:
        """从URL提取或生成视频ID"""
        # 尝试从URL提取YouTube视频ID
        if "youtube.com/watch?v=" in url:
            video_id = url.split("watch?v=")[1].split("&")[0]
        elif "youtu.be/" in url:
            video_id = url.split("youtu.be/")[1].split("?")[0]
        else:
            # 如果不是标准YouTube URL，使用MD5哈希
            video_id = hashlib.md5(url.encode()).hexdigest()
        return video_id
    
    def is_processed(self, url: str) -> bool:
        """检查视频是否已处理"""
        video_id = self.get_video_id(url)
        return video_id in self.cache
    
    def mark_processed(self, url: str, metadata: Optional[Dict] = None):
        """标记视频为已处理"""
        video_id = self.get_video_id(url)
        self.cache[video_id] = {
            "url": url,
            "processed_at": datetime.now().isoformat(),
            "metadata": metadata or {}
        }
        self._save_cache()
    
    def mark_failed(self, url: str, error: str):
        """标记视频处理失败"""
        video_id = self.get_video_id(url)
        self.cache[video_id] = {
            "url": url,
            "failed_at": datetime.now().isoformat(),
            "error": error,
            "status": "failed"
        }
        self._save_cache()

    def mark_uploaded(self, url: str, bvid: str):
        """标记视频已上传到B站"""
        video_id = self.get_video_id(url)
        if video_id not in self.cache:
            return
        self.cache[video_id].setdefault('metadata', {}).update({
            'upload_status': 'uploaded',
            'bvid': bvid,
            'uploaded_at': datetime.now().isoformat(),
        })
        self._save_cache()

    def mark_upload_failed(self, url: str, error: str):
        """标记视频上传B站失败"""
        video_id = self.get_video_id(url)
        if video_id not in self.cache:
            return
        self.cache[video_id].setdefault('metadata', {}).update({
            'upload_status': 'upload_failed',
            'upload_error': error,
        })
        self._save_cache()
    
    def get_status(self, url: str) -> Optional[Dict]:
        """获取视频处理状态"""
        video_id = self.get_video_id(url)
        return self.cache.get(video_id)
    
    def remove_entry(self, url: str):
        """从缓存中移除条目（用于重新处理）"""
        video_id = self.get_video_id(url)
        if video_id in self.cache:
            del self.cache[video_id]
            self._save_cache()
    
    def get_all_processed(self) -> List[str]:
        """获取所有已处理的视频URL"""
        return [entry.get("url", "") for entry in self.cache.values() 
                if entry.get("status") != "failed"]
    
    def get_statistics(self) -> Dict:
        """获取缓存统计信息"""
        total = len(self.cache)
        successful = sum(1 for entry in self.cache.values() 
                        if entry.get("status") != "failed")
        failed = total - successful
        
        return {
            "total": total,
            "successful": successful,
            "failed": failed
        }
