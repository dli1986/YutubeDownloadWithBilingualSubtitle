"""
字幕合并模块
合并中英文字幕，确保时间轴对齐
基于已有的bilingual_merge.py进行增强
"""

import pysrt
import logging
from typing import Dict, List


logger = logging.getLogger(__name__)


class SubtitleMerger:
    """字幕合并器"""
    
    def __init__(self, config: Dict):
        self.config = config.get('subtitle_merger', {})
        self.output_format = self.config.get('output_format', 'srt')
        self.layout = self.config.get('layout', 'vertical')
        self.time_threshold = self.config.get('time_threshold', 500)
    
    def merge_bilingual(self, en_srt_path: str, zh_srt_path: str, out_path: str) -> bool:
        """
        合并中英文字幕
        en_srt_path: 英文字幕路径
        zh_srt_path: 中文字幕路径
        out_path: 输出路径
        """
        try:
            subs_en = pysrt.open(en_srt_path, encoding='utf-8')
            subs_zh = pysrt.open(zh_srt_path, encoding='utf-8')
            
            logger.info(f"英文字幕: {len(subs_en)} 条")
            logger.info(f"中文字幕: {len(subs_zh)} 条")
            
            merged = self._merge_subtitles(subs_en, subs_zh)

            # 防御性终止时间钳位：确保相邻双语条目不重叠
            # （重叠会导致 libass 同时渲染多条，堆叠行数将字幕整个块推高）
            for i in range(len(merged) - 1):
                curr = merged[i]
                nxt  = merged[i + 1]
                if curr.end.ordinal > nxt.start.ordinal:
                    curr.end.ordinal = nxt.start.ordinal

            # 保存合并后的字幕
            merged.save(out_path, encoding='utf-8')
            logger.info(f"双语字幕合并完成: {out_path}, 共 {len(merged)} 条")
            return True

        except Exception as e:
            logger.error(f"字幕合并失败: {e}")
            return False
    
    def _merge_subtitles(self, subs_en: pysrt.SubRipFile, subs_zh: pysrt.SubRipFile) -> pysrt.SubRipFile:
        """
        合并两个字幕文件，处理时间轴对齐
        """
        merged = pysrt.SubRipFile()
        
        zh_index = 0
        zh_len = len(subs_zh)
        
        for en_sub in subs_en:
            # 找到最匹配的中文字幕
            best_zh = None
            best_overlap = 0
            
            # 从当前位置开始搜索
            for i in range(zh_index, min(zh_index + 10, zh_len)):  # 最多向后看10条
                zh_sub = subs_zh[i]
                
                # 计算时间重叠度
                overlap = self._calculate_overlap(en_sub, zh_sub)
                
                if overlap > 0 and overlap > best_overlap:
                    best_zh = zh_sub
                    best_overlap = overlap
                    zh_index = i
                
                # 如果中文字幕已经远超英文字幕，停止搜索
                if zh_sub.start.ordinal > en_sub.end.ordinal + self.time_threshold:
                    break
            
            # 构建双语文本
            if self.layout == 'vertical':
                # 垂直排列：英文在上，中文在下
                if best_zh:
                    text = f"{en_sub.text}\n{best_zh.text}"
                else:
                    text = en_sub.text
            else:
                # 水平排列（暂时也用垂直，可以后续扩展）
                if best_zh:
                    text = f"{en_sub.text}\n{best_zh.text}"
                else:
                    text = en_sub.text
            
            # 创建合并后的字幕项
            merged_sub = pysrt.SubRipItem(
                index=len(merged) + 1,
                start=en_sub.start,
                end=en_sub.end,
                text=text
            )
            merged.append(merged_sub)
        
        return merged
    
    def _calculate_overlap(self, sub1: pysrt.SubRipItem, sub2: pysrt.SubRipItem) -> float:
        """
        计算两个字幕的时间重叠度（毫秒）
        """
        overlap_start = max(sub1.start.ordinal, sub2.start.ordinal)
        overlap_end = min(sub1.end.ordinal, sub2.end.ordinal)
        
        overlap = overlap_end - overlap_start
        return max(0, overlap)
    
    def align_subtitles(self, subs: pysrt.SubRipFile, reference_subs: pysrt.SubRipFile) -> pysrt.SubRipFile:
        """
        将字幕对齐到参考字幕的时间轴
        用于修正时间偏移
        """
        aligned = pysrt.SubRipFile()
        
        for sub in subs:
            # 找到参考字幕中最接近的项
            best_ref = None
            min_diff = float('inf')
            
            for ref_sub in reference_subs:
                diff = abs(sub.start.ordinal - ref_sub.start.ordinal)
                if diff < min_diff:
                    min_diff = diff
                    best_ref = ref_sub
                
                # 如果差异太大，停止搜索
                if ref_sub.start.ordinal > sub.start.ordinal + 5000:
                    break
            
            # 如果找到匹配且差异在阈值内，使用参考时间
            if best_ref and min_diff < self.time_threshold:
                aligned_sub = pysrt.SubRipItem(
                    index=len(aligned) + 1,
                    start=best_ref.start,
                    end=best_ref.end,
                    text=sub.text
                )
            else:
                aligned_sub = pysrt.SubRipItem(
                    index=len(aligned) + 1,
                    start=sub.start,
                    end=sub.end,
                    text=sub.text
                )
            
            aligned.append(aligned_sub)
        
        return aligned
    
    def clean_subtitle_text(self, text: str) -> str:
        """
        清理字幕文本（移除多余的空格、换行等）
        """
        # 移除多余的空格
        text = ' '.join(text.split())
        # 移除音乐符号等
        text = text.replace('♪', '').replace('♫', '')
        return text.strip()
