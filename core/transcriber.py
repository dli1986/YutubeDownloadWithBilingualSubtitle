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

# ── Semantic Break Scorer (spaCy-backed, lazy-loaded) ────────────────────────
class SemanticBreakScorer:
    """
    单例、延迟加载的语义断点评分器。

    基于 spaCy dependency parse 评估在两个相邻词之间断句的语义安全性。
    返回 "break score"（0.0–1.0），越接近 1.0 表示该位置越安全可断。

    分层规则（各司其职）：
    ┌─ Layer 1: Dependency head constraint ──────────────────────────────────┐
    │  左侧词是右侧词的 direct modifier（compound/amod/det/poss/nummod/nmod）→ BLOCK
    └─────────────────────────────────────────────────────────────────────────┘
    ┌─ Layer 2: Punctuation / sentence boundary ──────────────────────────────┐
    │  左侧词带终止标点（.!?） → ALLOW (score=1.0)                              │
    │  左侧词带逗号/分号      → ALLOW with slight penalty (0.8)                │
    └─────────────────────────────────────────────────────────────────────────┘
    ┌─ Layer 3: POS / dependency role of right token ─────────────────────────┐
    │  右侧词是主句 ROOT/VERB/SUBJ 开头 → ALLOW (好的断点)                      │
    │  右侧词是从属连词/介词/限定词     → PENALIZE                              │
    └─────────────────────────────────────────────────────────────────────────┘
    ┌─ Layer 4: Open dependency arcs ─────────────────────────────────────────┐
    │  统计跨越断点左右侧的 open arcs 数量；arc 越多 → 语义联系越紧              │
    └─────────────────────────────────────────────────────────────────────────┘
    """

    _instance = None
    _nlp = None
    _cache: dict = {}           # text → spaCy Doc（避免重复解析相同 group）
    MAX_CACHE = 200

    @classmethod
    def get(cls) -> 'SemanticBreakScorer':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load(self):
        if self._nlp is not None:
            return
        try:
            import spacy
            self._nlp = spacy.load('en_core_web_sm', disable=['ner', 'lemmatizer'])
            logging.getLogger(__name__).info('SemanticBreakScorer: spaCy en_core_web_sm loaded')
        except Exception as e:
            logging.getLogger(__name__).warning(f'SemanticBreakScorer: spaCy unavailable ({e}), using heuristic fallback')
            self._nlp = None

    def _parse(self, text: str):
        """Parse text → spaCy Doc, with cache."""
        self._load()
        if self._nlp is None:
            return None
        key = text[:200]
        if key not in self._cache:
            if len(self._cache) >= self.MAX_CACHE:
                # evict oldest (FIFO)
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = self._nlp(text)
        return self._cache[key]

    # ── Dependency / open-arc helpers ─────────────────────────────────────────
    @staticmethod
    def _open_arcs(doc, split_idx: int) -> int:
        """Count dependency arcs that cross the split boundary (token idx split_idx → split_idx+1)."""
        count = 0
        for token in doc:
            lo = min(token.i, token.head.i)
            hi = max(token.i, token.head.i)
            if lo <= split_idx < hi:
                count += 1
        return count

    BLOCKING_DEPS = frozenset({
        'compound', 'amod', 'det', 'poss', 'nummod', 'nmod', 'npadvmod',
        'quantmod', 'predet', 'nn',
    })
    OPEN_COMP_DEPS = frozenset({
        'prep', 'mark', 'relcl', 'acl', 'advcl', 'xcomp', 'ccomp', 'pcomp',
    })
    VERB_ARG_DEPS = frozenset({
        'prep', 'dobj', 'xcomp', 'ccomp', 'attr', 'acomp', 'agent', 'oprd',
    })
    # Locative/directional adverbs that complete a verb phrase and must not
    # be separated from their head verb (e.g. "find what you need | there")
    LOCATIVE_ADVS = frozenset({
        'there', 'here', 'away', 'back', 'out', 'ahead',
        'forward', 'together', 'along', 'apart',
    })
    OPEN_CONJ_DEPS = frozenset({'conj', 'cc', 'prep', 'pobj', 'relcl', 'acl', 'advcl'})
    BAD_RIGHT_POS  = frozenset({'DET', 'ADP', 'CCONJ', 'SCONJ', 'PART'})
    GOOD_RIGHT_DEPS = frozenset({'ROOT', 'nsubj', 'nsubjpass', 'csubj', 'expl'})

    def score(self, left_text: str, right_text: str) -> float:
        """
        Score the break BETWEEN left_text and right_text.
        Returns 0.0 (never break) … 1.0 (safe to break).

        Falls back to heuristic-only (0.5) when spaCy unavailable.
        """
        # ── Heuristic Layer 0: sentence boundary ──────────────────────────
        left_strip = left_text.rstrip()
        if left_strip and left_strip[-1] in '.!?':
            return 1.0
        if left_strip and left_strip[-1] in ',;:':
            return 0.8

        # ── Heuristic Layer 1: left ends with obvious open dependency ──────
        left_last = left_text.split()[-1].lower().rstrip('.,!?;:"\'-') if left_text.split() else ''
        right_first = right_text.split()[0].lower().rstrip('.,!?;:"\'-') if right_text.split() else ''

        FUNC = {'the','a','an','in','of','to','for','on','at','by','with','from',
                'and','or','but','so','yet','as','that','which','who','if','not',
                'into','onto','upon','within','without','between','after','before',
                'since','while','though','because','although','when','where',
                'very','quite','rather','just','also','only','actually','even',
                'it','its','this','their','our','be','been','have','is','are',
                'was','were','will','would','could','should','might','may',
                'do','does','did','has','had'}
        if left_last in FUNC:
            return 0.0     # hard block – left hangs open

        # ── spaCy parse of the combined span ──────────────────────────────
        combined = (left_text + ' ' + right_text).strip()
        doc = self._parse(combined)
        if doc is None:
            # No spaCy: use expanded heuristic
            return 0.3 if right_first in FUNC else 0.6

        # Find split position using character offsets so hyphenated/punctuated
        # words tokenized into multiple spaCy tokens (e.g. "open-source" → 3
        # tokens) don't shift the boundary.
        left_char_end = len(left_text)  # right text starts at char ≥ this
        split_idx = -1
        for tok in doc:
            if tok.idx < left_char_end:
                split_idx = tok.i
            else:
                break

        if split_idx < 0 or split_idx >= len(doc) - 1:
            return 0.5

        left_tok  = doc[split_idx]
        right_tok = doc[split_idx + 1]

        # ── Layer 1: Direct modifier → BLOCK ──────────────────────────────
        # If right_tok modifies left_tok or vice versa with a blocking dep
        if right_tok.dep_ in self.BLOCKING_DEPS and right_tok.head.i == left_tok.i:
            return 0.0
        if left_tok.dep_ in self.BLOCKING_DEPS and left_tok.head.i == right_tok.i:
            return 0.05    # left modifies right (e.g. compound chain), very unsafe

        # ── Layer 2.5: Open complement — right depends on left-span item ──
        # Case A: right_tok is prep/mark/relcl/etc. directly attached to left span
        # e.g. "We're building duckling | for developers"
        if right_tok.dep_ in self.OPEN_COMP_DEPS and right_tok.head.i <= split_idx:
            return 0.0
        # Case B: right_tok starts a relative/adjectival clause modifying left-span noun
        # e.g. "the Python SDK | which comes with it" — 'which' is nsubj of
        # 'comes' (relcl → SDK); SDK is in the left span → hard block
        if right_tok.head.dep_ in ('relcl', 'acl', 'rcmod', 'acl:relcl') and \
           right_tok.head.head.i <= split_idx:
            return 0.0

        # ── Layer 2.6: Phrase root anchored in left span ──────────────────
        # e.g. "in the open-source | document AI space."
        # 'document' is compound→'space'; 'space' is pobj→'in' (left) → hard block
        if right_tok.dep_ in self.BLOCKING_DEPS:
            head = right_tok
            for _ in range(5):
                if head.dep_ not in self.BLOCKING_DEPS:
                    break
                head = head.head
            if head.i > split_idx and head.head.i <= split_idx:
                return 0.0

        # ── Layer 2.7: Predicate completion — verb has open arg in right ──
        # e.g. "We're building duckling | for developers..."
        # 'building' VERB with 'for' (prep child) at index > split_idx → 0.0
        for tok in doc[:split_idx + 1]:
            if tok.pos_ in ('VERB', 'AUX') and \
               tok.dep_ in ('ROOT', 'ccomp', 'advcl', 'xcomp', 'relcl', 'acl'):
                for child in tok.children:
                    if child.i > split_idx:
                        if child.dep_ in self.VERB_ARG_DEPS:
                            return 0.0  # Verb argument incomplete — never break here
                        # Layer 2.7b: locative/directional advmod completes the VP
                        # e.g. "find what you need | there"
                        if child.dep_ == 'advmod' and child.text.lower() in self.LOCATIVE_ADVS:
                            return 0.0  # Locative completion missing — never break here

        # ── Layer 2: right is good sentence start → ALLOW ─────────────────
        if right_tok.dep_ in self.GOOD_RIGHT_DEPS or right_tok.dep_ == 'ROOT':
            return 0.9

        # ── Layer 3: right POS is bad (det/prep/conj/part) → PENALIZE ─────
        if right_tok.pos_ in self.BAD_RIGHT_POS:
            # Exception: CCONJ opening a coordinated clause with its own subject
            # e.g. "...in corporate contexts | And you can find..."
            # Discourse shift → allow the break (score above SAFE_BREAK_THRESHOLD)
            if right_tok.pos_ == 'CCONJ':
                for sibling in right_tok.head.children:
                    if sibling.dep_ in ('nsubj', 'nsubjpass', 'expl') \
                            and sibling.i > split_idx:
                        return 0.60  # Discourse shift — new clause with subject
            return 0.1

        # ── Layer 4: Open arc count ────────────────────────────────────────
        arcs = self._open_arcs(doc, split_idx)
        # 0 arcs = clean boundary, ≥3 arcs = very entangled
        arc_penalty = min(arcs, 4) / 4.0          # 0.0 … 1.0
        base = 0.7 - arc_penalty * 0.5            # 0.20 … 0.70

        # small bonus if left_tok ends a clause (VERB / AUX root of subclause)
        if left_tok.pos_ in ('VERB', 'AUX') and left_tok.dep_ in ('ROOT', 'ccomp', 'advcl', 'relcl'):
            base = min(base + 0.15, 1.0)

        return round(base, 3)
# ─────────────────────────────────────────────────────────────────────────────


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

            # 第三遍：折叠 YouTube 滚动窗口字幕
            # 每个 VTT entry 显示约 2 行文本，后一条开头重复前一条结尾
            # 提取每条 entry 中的"新增部分"，按句子边界重新分段

            def _ms_to_srt_time(ms: int) -> str:
                h  = ms // 3_600_000
                m  = (ms % 3_600_000) // 60_000
                s  = (ms % 60_000) // 1_000
                f  = ms % 1_000
                return f"{h:02d}:{m:02d}:{s:02d},{f:03d}"

            def _get_new_suffix(prev: str, curr: str) -> str:
                """返回 curr 中不与 prev 末尾重叠的新增部分。"""
                prev_words = prev.split()
                curr_words = curr.split()
                pl = [w.lower().rstrip('.,!?') for w in prev_words]
                cl = [w.lower().rstrip('.,!?') for w in curr_words]
                max_try = min(len(pl), len(cl))
                for n in range(max_try, 0, -1):
                    if pl[-n:] == cl[:n]:
                        return ' '.join(curr_words[n:]).strip()
                return curr.strip()

            def _is_rolling(blocks) -> bool:
                """检测是否为滚动窗口字幕（超过50%相邻对有显著重叠）。"""
                if len(blocks) < 4:
                    return False
                sample = min(10, len(blocks) - 1)
                votes = 0
                for i in range(1, sample + 1):
                    prev_t = blocks[i - 1][3]
                    curr_t = blocks[i][3]
                    new_part = _get_new_suffix(prev_t, curr_t)
                    if len(new_part.split()) < len(curr_t.split()) * 0.65:
                        votes += 1
                return votes >= sample * 0.5

            def _collapse_rolling(blocks) -> list:
                """将滚动字幕折叠为句级分段。"""
                # 提取每条 entry 的"新增文本"片段
                fragments = []  # (start_ms, end_ms, new_text)
                for i, (start_ms, end_ms, _, text) in enumerate(blocks):
                    new_text = text.strip() if i == 0 else _get_new_suffix(blocks[i - 1][3], text)
                    if new_text:
                        fragments.append((start_ms, end_ms, new_text))

                # 按句子边界或时长/长度上限合并为结果块
                MAX_MS   = 6_000   # 最长 6 秒
                MAX_CHAR = 150
                result   = []
                buf      = []      # (start_ms, end_ms, text) 列表
                for frag in fragments:
                    buf.append(frag)
                    combined  = ' '.join(t for _, _, t in buf)
                    duration  = buf[-1][1] - buf[0][0]
                    ends_sent = bool(re.search(r'[.!?]\s*$', frag[2]))
                    if ends_sent or len(combined) > MAX_CHAR or duration > MAX_MS:
                        s_ms = buf[0][0]
                        e_ms = buf[-1][1]
                        tl   = f"{_ms_to_srt_time(s_ms)} --> {_ms_to_srt_time(e_ms)}"
                        result.append((s_ms, e_ms, tl, combined))
                        buf = []
                if buf:
                    combined = ' '.join(t for _, _, t in buf)
                    s_ms = buf[0][0]
                    e_ms = buf[-1][1]
                    tl   = f"{_ms_to_srt_time(s_ms)} --> {_ms_to_srt_time(e_ms)}"
                    result.append((s_ms, e_ms, tl, combined))
                return result

            if _is_rolling(merged):
                logger.info("检测到 YouTube 滚动窗口字幕，正在折叠为句级分段…")
                merged = _collapse_rolling(merged)
                logger.info(f"折叠完成：{len(merged)} 条句级字幕")

            # 第四遍：写入 SRT
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
    def srv3_to_srt(self, srv3_path: str, srt_path: str) -> bool:
        """
        将 YouTube srv3（XML）字幕转换为 SRT 格式。
        srv3 是 YouTube 字幕的原始格式，每个 <p> 段落离散不重叠，
        与网页端 CC 显示一致，无需滚动窗口去重处理。
        """
        try:
            import xml.etree.ElementTree as ET

            tree = ET.parse(srv3_path)
            root = tree.getroot()

            def _ms_to_srt(ms: int) -> str:
                h = ms // 3_600_000
                m = (ms % 3_600_000) // 60_000
                s = (ms % 60_000) // 1_000
                f = ms % 1_000
                return f"{h:02d}:{m:02d}:{s:02d},{f:03d}"

            entries = []
            # srv3 结构：<timedtext><body><p t="..." d="...">text</p>...
            body = root.find('body')
            if body is None:
                body = root  # 兜底

            for p in body.iter('p'):
                t_ms = int(p.get('t', 0))
                d_ms = int(p.get('d', 0))
                if d_ms <= 0:
                    continue
                end_ms = t_ms + d_ms

                # 提取文本：可能是 <p> 直接文本，或含 <s> 子元素的词级标注
                parts = []
                if p.text and p.text.strip():
                    parts.append(p.text.strip())
                for s in p:
                    if s.tag == 's':
                        if s.text and s.text.strip():
                            parts.append(s.text.strip())
                        if s.tail and s.tail.strip():
                            parts.append(s.tail.strip())
                    elif s.tail and s.tail.strip():
                        parts.append(s.tail.strip())

                text = ' '.join(parts)
                text = html.unescape(text)
                text = re.sub(r'\s+', ' ', text).strip()
                if not text:
                    continue

                entries.append((t_ms, end_ms, text))

            if not entries:
                logger.warning(f"srv3 文件中未找到有效字幕段落: {srv3_path}")
                return False

            srt_lines = []
            for idx, (t_ms, end_ms, text) in enumerate(entries, 1):
                srt_lines.append(
                    f"{idx}\n{_ms_to_srt(t_ms)} --> {_ms_to_srt(end_ms)}\n{text}\n"
                )

            with open(srt_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(srt_lines))

            logger.info(f"srv3转SRT成功: {srt_path}（{len(entries)} 条）")
            return True
        except Exception as e:
            logger.error(f"srv3转SRT失败: {e}")
            return False

    def json3_to_srt(self, json3_path: str, srt_path: str) -> bool:
        """
        将 YouTube json3 字幕转换为 SRT 格式。

        策略：完全按照 json3 的 event 结构直接渲染。
          - 每个无 aAppend 的 event = 一条 SRT 条目
          - 时间戳直接使用 tStartMs / (tStartMs + dDurationMs)
          - 只做末尾钳位（将 end 限制为下一条 start）以消除重叠
          - 不做任何合并、分句或停顿检测
        """
        try:
            import json

            with open(json3_path, encoding='utf-8') as f:
                data = json.load(f)

            def _ms_to_srt(ms: int) -> str:
                h = ms // 3_600_000
                m = (ms % 3_600_000) // 60_000
                s = (ms % 60_000) // 1_000
                f = ms % 1_000
                return f"{h:02d}:{m:02d}:{s:02d},{f:03d}"

            entries = []  # (start_ms, end_ms, text)
            for ev in data.get('events', []):
                if ev.get('aAppend', 0) == 1:
                    continue  # 滚动分隔符，跳过
                segs = ev.get('segs')
                if not segs:
                    continue
                t_base = ev.get('tStartMs', 0)
                d_ms = ev.get('dDurationMs', 0)
                if d_ms <= 0:
                    continue

                # 拼接本 event 所有词；首词可能无前导空格，跨词补空格
                text = ''
                for seg in segs:
                    w = seg.get('utf8', '')
                    if not w or w == '\n':
                        continue
                    if text and not text[-1].isspace() and not w[0].isspace():
                        text += ' '
                    text += w
                text = re.sub(r'\s+', ' ', text).strip()
                if not text:
                    continue

                entries.append((t_base, t_base + d_ms, text))

            if not entries:
                logger.warning(f"json3 文件中未找到有效条目: {json3_path}")
                return False

            # 末尾钳位：消除相邻条目的时间重叠
            for i in range(len(entries) - 1):
                s_i, e_i, t_i = entries[i]
                s_next = entries[i + 1][0]
                if e_i > s_next:
                    entries[i] = (s_i, s_next, t_i)

            srt_lines = []
            for idx, (start, end, text) in enumerate(entries, 1):
                srt_lines.append(
                    f"{idx}\n{_ms_to_srt(start)} --> {_ms_to_srt(end)}\n{text}\n"
                )

            with open(srt_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(srt_lines))

            logger.info(f"json3转SRT成功: {srt_path}（{len(entries)} 条）")
            return True
        except Exception as e:
            logger.error(f"json3转SRT失败: {e}")
            return False

    def json3_extract_words(self, json3_path: str) -> list:
        """
        从 json3 提取词级时间流。
        返回 [{'abs_ms': int, 'dur_ms': int, 'word': str}, ...]
        aAppend 滚动分隔符事件跳过。
        """
        try:
            import json
            with open(json3_path, encoding='utf-8') as f:
                data = json.load(f)

            words = []
            for ev in data.get('events', []):
                if ev.get('aAppend', 0) == 1:
                    continue
                segs = ev.get('segs')
                if not segs:
                    continue
                t_base = ev.get('tStartMs', 0)
                d_ev   = ev.get('dDurationMs', 0)

                # 过滤有效词，记录 (word, tOffsetMs)
                ev_words = [
                    (seg.get('utf8', ''), seg.get('tOffsetMs', 0))
                    for seg in segs
                    if seg.get('utf8', '') and seg.get('utf8') != '\n'
                ]
                for i, (word, t_off) in enumerate(ev_words):
                    abs_ms = t_base + t_off
                    if i < len(ev_words) - 1:
                        # 词持续到下一词开始
                        dur_ms = (t_base + ev_words[i + 1][1]) - abs_ms
                    else:
                        # 最后一词：持续到 event 结束或默认 200ms
                        dur_ms = max(200, (t_base + d_ev) - abs_ms) if d_ev > 0 else 200
                    words.append({'abs_ms': abs_ms, 'dur_ms': max(50, dur_ms), 'word': word})

            logger.info(f"json3词流提取完成：{len(words)} 词 ← {json3_path}")
            return words
        except Exception as e:
            logger.error(f"json3词流提取失败: {e}")
            return []

    # ── Deterministic segmentation ─────────────────────────────────────────

    def compute_boundary_scores(self, words: list, context_window: int = 8) -> list:
        """
        Compute a deterministic break score ∈ [0.0, 1.0] for every word boundary.

        boundary_scores[i]  = score for the gap AFTER words[i]
                              (between words[i] and words[i+1])

        Scoring components (in priority order):
          1. Sentence-ending punctuation (.!?)  → 1.0 (immediate return)
          2. SemanticBreakScorer ≤ 0.05         → 0.0 (hard block, immediate return)
          3. Combined:
               max(punct_s,  0.55 * sem_s  +  0.45 * pause_s)
             where:
               punct_s  = 0.7 if ,;: else 0.0
               pause_s  = min(1.0, gap_ms / 1500)
               sem_s    = SemanticBreakScorer.score(left_ctx, right_ctx)

        Returns list of length len(words)-1.
        """
        LONG_PAUSE_MS = 1500
        HARD_BLOCK    = 0.05   # SemanticBreakScorer threshold for "never break"

        scorer = SemanticBreakScorer.get()

        def _join_w(ws):
            text = ''
            for w in ws:
                wt = w['word']
                if text and not text[-1].isspace() and not wt[0].isspace():
                    text += ' '
                text += wt
            return re.sub(r'\s+', ' ', text).strip()

        scores = []
        for i in range(len(words) - 1):
            last_char = words[i]['word'].rstrip()[-1:]

            # Sentence-ending punctuation: always a strong break
            if last_char in '.!?':
                scores.append(1.0)
                continue

            # Comma / semicolon / colon: partial punctuation signal
            punct_s = 0.7 if last_char in ',;:' else 0.0

            # Pause duration signal
            end_i   = words[i]['abs_ms'] + words[i]['dur_ms']
            gap     = max(0, words[i + 1]['abs_ms'] - end_i)
            pause_s = min(1.0, gap / LONG_PAUSE_MS)

            # Semantic signal (context window around boundary)
            lo = max(0, i - context_window + 1)
            hi = min(len(words), i + 1 + context_window)
            left_text  = _join_w(words[lo:i + 1])
            right_text = _join_w(words[i + 1:hi])
            sem_s = scorer.score(left_text, right_text) if right_text else 1.0

            # Hard block: semantic constraint says never break here
            if sem_s <= HARD_BLOCK:
                scores.append(0.0)
                continue

            combined = max(punct_s, round(0.55 * sem_s + 0.45 * pause_s, 3))
            scores.append(combined)

        return scores

    def build_groups_from_scores(
        self,
        words: list,
        boundary_scores: list,
        max_chars: int = 70,
        max_dur_ms: int = 7000,
        llm_votes: dict = None,
    ) -> list:
        """
        Build subtitle groups from deterministic boundary scores + optional LLM votes.

        llm_votes = {word_idx: bool}  True = LLM suggests break, False = LLM says keep
        LLM influence is capped at LLM_WEIGHT=0.25 (advisory only).
        Hard blocks (score == 0.0) are NEVER overridden by LLM.

        Break decision rules (in priority order):
          1. score == 0.0  → never break (hard semantic block)
          2. score >= 0.75 → always break (strong boundary)
          3. next word would exceed CHARS_HARD (max_chars*1.4) or max_dur_ms → force break
          4. approaching max_chars AND score >= BREAK_THRESHOLD (0.45) → prefer break

        Returns [[word_idx,...], ...]  (0-based)
        """
        if not words:
            return []

        LLM_WEIGHT      = 0.25
        STRONG_BREAK    = 0.75
        BREAK_THRESHOLD = 0.45
        CHARS_HARD      = int(max_chars * 1.4)   # absolute force-break limit

        llm_votes = llm_votes or {}

        # Apply bounded LLM adjustments (copy to avoid mutating original)
        scores = list(boundary_scores)
        for idx, voted_yes in llm_votes.items():
            if 0 <= idx < len(scores) and scores[idx] > 0.0:   # never touch hard blocks
                delta = LLM_WEIGHT * (1.0 if voted_yes else -1.0)
                scores[idx] = round(max(0.01, min(1.0, scores[idx] + delta)), 3)

        def _join_w(ws):
            text = ''
            for w in ws:
                wt = w['word']
                if text and not text[-1].isspace() and not wt[0].isspace():
                    text += ' '
                text += wt
            return re.sub(r'\s+', ' ', text).strip()

        groups    = []
        cur_start = 0

        for i in range(len(words) - 1):
            sc = scores[i]

            # Rule 1: hard block
            if sc == 0.0:
                continue

            # Rule 2: strong boundary → always break
            if sc >= STRONG_BREAK:
                groups.append(list(range(cur_start, i + 1)))
                cur_start = i + 1
                continue

            # Constraint metrics for current group
            cur_text   = _join_w(words[cur_start:i + 1])
            cur_chars  = len(cur_text)
            next_chars = len(words[i + 1]['word'].strip())
            after_add  = cur_chars + 1 + next_chars
            cur_dur    = (words[i]['abs_ms'] + words[i]['dur_ms']) - words[cur_start]['abs_ms']

            # Rule 3: hard limits hit → force break
            if after_add > CHARS_HARD or cur_dur > max_dur_ms:
                groups.append(list(range(cur_start, i + 1)))
                cur_start = i + 1

            # Rule 4: approaching limit + good-enough score → prefer break
            elif after_add > max_chars and sc >= BREAK_THRESHOLD:
                groups.append(list(range(cur_start, i + 1)))
                cur_start = i + 1

        # Flush final group
        if cur_start < len(words):
            groups.append(list(range(cur_start, len(words))))

        return groups

    # ──────────────────────────────────────────────────────────────────────

    def words_to_srt(self, words: list, groups: list, srt_path: str,
                     max_cps: int = 20, max_chars_per_line: int = 42,
                     max_lines: int = 2, min_dur_ms: int = 833,
                     max_dur_ms: int = 7000, min_gap_ms: int = 83) -> bool:
        """
        将词组列表转换为 SRT，应用工业级展示规则（Netflix 风格）。

        words : [{'abs_ms', 'dur_ms', 'word'}, ...]
        groups: [[word_idx, ...], ...]  (0-based)

        展示规则：
          - 每行 ≤ max_chars_per_line 字符；超出则换行（最多 max_lines 行）
          - 显示时长：min_dur_ms ≤ dur ≤ max_dur_ms
          - CPS（字符/秒）≤ max_cps；超速时适当延长时长
          - 相邻条目间隔 ≥ min_gap_ms
        """
        try:
            def _ms_to_srt(ms: int) -> str:
                h = ms // 3_600_000
                m = (ms % 3_600_000) // 60_000
                s = (ms % 60_000) // 1_000
                f = ms % 1_000
                return f"{h:02d}:{m:02d}:{s:02d},{f:03d}"

            def _join_words(word_dicts: list) -> str:
                """拼接词文本，处理跨 event 边界缺失前导空格的情况。"""
                text = ''
                for w in word_dicts:
                    wt = w['word']
                    if text and not text[-1].isspace() and not wt[0].isspace():
                        text += ' '
                    text += wt
                return re.sub(r'\s+', ' ', text).strip()

            def _format_lines(text: str) -> str:
                """
                按行宽规则格式化文本。
                ≤ max_chars_per_line → 单行；超出则在最接近中点的空格处断行。
                """
                if len(text) <= max_chars_per_line:
                    return text
                target = len(text) // 2
                # 向左找最近空格
                idx = text.rfind(' ', 0, target + 1)
                if idx == -1:
                    idx = text.find(' ', target)
                if idx == -1 or max_lines < 2:
                    return text  # 无法断行则原样输出
                line1 = text[:idx].strip()
                line2 = text[idx + 1:].strip()
                # 第二行仍超长时硬截断（保底）
                if len(line2) > max_chars_per_line:
                    line2 = line2[:max_chars_per_line]
                return f"{line1}\n{line2}"

            # ── CC 语义原子性常量 ─────────────────────────────────────────────
            # 不允许 subtitle block 以这些功能词结尾（Minimum Semantic Load Rule）
            CC_FUNC_WORDS = frozenset({
                'the', 'a', 'an', 'in', 'of', 'to', 'for', 'on', 'at', 'by',
                'with', 'from', 'and', 'or', 'but', 'so', 'yet', 'as', 'that',
                'which', 'who', 'when', 'where', 'if', 'than', 'about', 'over',
                'into', 'onto', 'upon', 'within', 'without', 'between', 'after',
                'before', 'since', 'while', 'though', 'what', 'how', 'not',
                # Determiners / quantifiers that cannot close a subtitle block
                'every', 'each', 'both', 'such', 'this', 'these', 'those',
                # Degree / focus adverbs that require a following adj/adv/NP
                'very', 'quite', 'rather', 'just', 'only', 'even', 'also',
                'already', 'still', 'actually', 'really', 'simply', 'truly',
            })
            # Fragment words: locative/directional adverbs that form uninterpretable
            # subtitle blocks when isolated (≤ 2 tokens).
            CC_FRAG_WORDS = frozenset({
                'there', 'here', 'away', 'back', 'out', 'ahead',
                'forward', 'together', 'along', 'apart',
            })
            # 数量词集合（NP Head-completion Rule）
            QUANTIFIERS = frozenset({
                'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight',
                'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen',
                'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen',
                'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy',
                'eighty', 'ninety', 'hundred', 'thousand', 'million', 'billion',
                'many', 'several', 'few', 'some', 'most', 'more', 'less',
                'any', 'every', 'each', 'both', 'all', 'no',
            })
            SENT_CONJ = frozenset({'and', 'or', 'but', 'so', 'yet'})
            SPLIT_TOLERANCE = max_chars_per_line * 1.3  # 子组切割时的最大容忍宽度
            SAFE_BREAK_THRESHOLD = 0.45   # score ≥ this → safe to cut
            scorer = SemanticBreakScorer.get()

            def _cc_safe_groups(glist: list) -> list:
                """
                CC 语义安全预处理（两轮扫描，处理基本级联）。

                规则 1 – Minimum Semantic Load（最小语义载荷）：
                  group 以功能词结尾 → 向前合并（并入下一 group）。
                规则 2 – Single-word（单词孤块）：
                  单词 group → 向后合并。
                规则 3 – NP Head-completion（名词短语完整性）：
                  1-2 词 group 且前 group 末为数量词/数字 → 向后合并。
                规则 4 – Semantic Break Score（spaCy dependency）：
                  group 末尾 break score < SAFE_BREAK_THRESHOLD → 向前合并。
                """
                MAX_MERGE_GAP_MS = 1000  # 跨停顿合并上限（ms）

                def _gtext(g):
                    gw2 = [words[idx] for idx in g if idx < len(words)]
                    return _join_words(gw2).strip() if gw2 else ''

                def _inter_gap(g_cur, g_nxt):
                    """g_cur 末词结束 → g_nxt 首词开始 的间隔（ms），范围 0+。"""
                    if not g_cur or not g_nxt:
                        return 0
                    wi = g_cur[-1] if g_cur[-1] < len(words) else None
                    wj = g_nxt[0]  if g_nxt[0]  < len(words) else None
                    if wi is None or wj is None:
                        return 0
                    end_i = words[wi]['abs_ms'] + words[wi].get('dur_ms', 200)
                    return max(0, words[wj]['abs_ms'] - end_i)

                for _ in range(2):
                    result: list = []
                    i = 0
                    gl = list(glist)
                    while i < len(gl):
                        g = gl[i]
                        text2 = _gtext(g)
                        ws = text2.replace('\n', ' ').split()
                        if not ws:
                            i += 1
                            continue
                        last = ws[-1].lower().rstrip('.,!?;:"\'-')

                        # Rule 1: ends with function word → forward merge (if gap small enough)
                        if last in CC_FUNC_WORDS and i + 1 < len(gl) \
                                and _inter_gap(g, gl[i + 1]) <= MAX_MERGE_GAP_MS:
                            gl[i + 1] = g + gl[i + 1]
                            i += 1
                            continue

                        # Rule 2: single-word group → backward merge
                        if len(ws) == 1:
                            word_lower = ws[0].lower().rstrip('.,!?;:"\'-')
                            if word_lower in SENT_CONJ and result:
                                prev_t = _gtext(result[-1]).rstrip()
                                if prev_t and prev_t[-1] in '.!?' \
                                        and i + 1 < len(gl) \
                                        and _inter_gap(g, gl[i + 1]) <= MAX_MERGE_GAP_MS:
                                    gl[i + 1] = g + gl[i + 1]
                                    i += 1
                                    continue
                            if result:
                                result[-1] = result[-1] + g
                                i += 1
                                continue
                            if i + 1 < len(gl) \
                                    and _inter_gap(g, gl[i + 1]) <= MAX_MERGE_GAP_MS:
                                gl[i + 1] = g + gl[i + 1]
                                i += 1
                                continue

                        # Rule 3: 1-2 word group + previous ends with quantifier/digit
                        if len(ws) <= 2 and result:
                            prev_ws = _gtext(result[-1]).replace('\n', ' ').split()
                            prev_last = prev_ws[-1].lower().rstrip('.,!?;:"\'-') if prev_ws else ''
                            if prev_last in QUANTIFIERS or (prev_last and prev_last.isdigit()):
                                result[-1] = result[-1] + g
                                i += 1
                                continue

                        # Rule 4: Semantic break score (spaCy) — merge if score too low
                        if i + 1 < len(gl) and _inter_gap(g, gl[i + 1]) <= MAX_MERGE_GAP_MS:
                            next_text = _gtext(gl[i + 1])
                            if next_text:
                                sc = scorer.score(text2, next_text)
                                if sc < SAFE_BREAK_THRESHOLD:
                                    merged_len = len(text2) + 1 + len(next_text)
                                    if merged_len <= max_chars_per_line * 1.5:
                                        gl[i + 1] = g + gl[i + 1]
                                        i += 1
                                        continue

                        # Rule 5: Fragment isolation — locative/directional words that
                        # cannot stand alone as a CC block (e.g. "there.", "back.")
                        if len(ws) <= 2 and all(
                                w.lower().rstrip('.,!?;:"\'') in CC_FRAG_WORDS for w in ws):
                            if result:
                                result[-1] = result[-1] + g
                                i += 1
                                continue
                            if i + 1 < len(gl) \
                                    and _inter_gap(g, gl[i + 1]) <= MAX_MERGE_GAP_MS:
                                gl[i + 1] = g + gl[i + 1]
                                i += 1
                                continue

                        result.append(g)
                        i += 1
                    glist = result
                return glist
            # ─────────────────────────────────────────────────────────────────

            groups = _cc_safe_groups(list(groups))
            entries = []  # (start_ms, end_ms, formatted_text)

            for group in groups:
                if not group:
                    continue
                gw = [words[i] for i in group if i < len(words)]
                if not gw:
                    continue

                text = _join_words(gw)
                if not text:
                    continue

                # ── 强制拆分过长组（语义安全版）──────────────────────────────
                # 结合 SemanticBreakScorer 决定切割时机：
                #   1. 句末标点 → 直接切（score=1.0）
                #   2. scorer.score(cur_text, rest_text) ≥ SAFE_BREAK_THRESHOLD → 切
                #   3. 超过 SPLIT_TOLERANCE → 强制切（保底，避免超宽行）
                sub_groups: list[list] = []
                cur: list = []
                cur_chars = 0
                for idx_w, w in enumerate(gw):
                    wt = w['word'].strip()
                    add = len(wt) + (1 if cur_chars > 0 else 0)
                    ends_sent = bool(cur) and cur[-1]['word'].rstrip()[-1:] in '.!?'
                    if cur and (cur_chars + add > max_chars_per_line or ends_sent):
                        if ends_sent:
                            sub_groups.append(cur)
                            cur = [w]
                            cur_chars = len(wt)
                        else:
                            cur_text  = _join_words(cur)
                            rest_text = _join_words(gw[idx_w:])
                            sc = scorer.score(cur_text, rest_text)
                            over_tolerance = cur_chars + add > SPLIT_TOLERANCE
                            HARD_BLOCK_THRESHOLD = 0.1  # score below this = never cut
                            # Guard: don't cut if the remaining words would form an
                            # uninterpretable fragment (≤ 2 locative/directional tokens)
                            _rem_clean = [
                                ww['word'].strip().lower().rstrip('.,!?;:')
                                for ww in gw[idx_w:]
                            ]
                            is_right_frag = (
                                len(_rem_clean) <= 2 and
                                all(t in CC_FRAG_WORDS for t in _rem_clean if t)
                            )
                            if (sc >= SAFE_BREAK_THRESHOLD or
                                    (over_tolerance and sc > HARD_BLOCK_THRESHOLD)) \
                                    and not is_right_frag:
                                sub_groups.append(cur)
                                cur = [w]
                                cur_chars = len(wt)
                            else:
                                cur.append(w)
                                cur_chars += add
                    else:
                        cur.append(w)
                        cur_chars += add
                if cur:
                    sub_groups.append(cur)
                # ────────────────────────────────────────────────────────────

                for sg in sub_groups:
                    sg_text = _join_words(sg)
                    if not sg_text:
                        continue

                    start_ms = sg[0]['abs_ms']
                    last_w   = sg[-1]
                    end_ms   = last_w['abs_ms'] + last_w['dur_ms']

                    # 时长约束
                    dur_ms = max(min_dur_ms, min(end_ms - start_ms, max_dur_ms))
                    end_ms = start_ms + dur_ms

                    # CPS 检查：阅读速率超标时延长显示时长
                    char_count = len(sg_text.replace('\n', ''))
                    if dur_ms > 0 and char_count / (dur_ms / 1000.0) > max_cps:
                        required_ms = int(char_count / max_cps * 1000)
                        end_ms = start_ms + min(required_ms, max_dur_ms)

                    entries.append((start_ms, end_ms, sg_text))

            if not entries:
                logger.warning("words_to_srt: 分组结果为空")
                return False

            # 相邻条目间隔保证（末尾钳位）
            for i in range(len(entries) - 1):
                s_i, e_i, t_i = entries[i]
                s_next = entries[i + 1][0]
                max_end = s_next - min_gap_ms
                if e_i > max_end:
                    entries[i] = (s_i, max(s_i + min_dur_ms, max_end), t_i)

            srt_lines = []
            for idx, (start, end, text) in enumerate(entries, 1):
                srt_lines.append(f"{idx}\n{_ms_to_srt(start)} --> {_ms_to_srt(end)}\n{text}\n")

            with open(srt_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(srt_lines))

            logger.info(f"words_to_srt 成功: {srt_path}（{len(entries)} 条）")
            return True
        except Exception as e:
            logger.error(f"words_to_srt 失败: {e}")
            return False