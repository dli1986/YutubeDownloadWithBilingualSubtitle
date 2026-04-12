"""
LLM翻译模块
支持多种LLM提供商（Ollama、OpenAI、Claude）进行字幕翻译
根据视频类型调整翻译风格
"""

import os
import re
import logging
import time
from typing import Dict, Optional, List
import pysrt
from tqdm import tqdm


logger = logging.getLogger(__name__)


class LLMTranslator:
    """LLM翻译器"""
    
    def __init__(self, config: Dict):
        self.config = config.get('translator', {})
        self.video_types_config = config.get('video_types', {})
        self.provider = self.config.get('default_provider', 'ollama')
        
        # 初始化各个提供商的客户端
        self._init_clients()
    
    def _init_clients(self):
        """初始化LLM客户端"""
        self.clients = {}
        
        # Ollama客户端
        try:
            import ollama
            host = self.config.get('ollama', {}).get('host', 'http://localhost:11434')
            self.clients['ollama'] = ollama.Client(host=host)
            # 测试连接
            try:
                self.clients['ollama'].list()
                logger.info(f"✓ Ollama客户端初始化成功 ({host})")
            except Exception as test_err:
                logger.error(f"Ollama服务连接失败 ({host}): {test_err}")
                logger.error("请确保Ollama服务正在运行：ollama serve")
                del self.clients['ollama']
        except ImportError:
            logger.warning("Ollama包未安装，请运行: pip install ollama")
        except Exception as e:
            logger.warning(f"Ollama客户端初始化失败: {e}")
        
        # OpenAI客户端
        try:
            from openai import OpenAI
            api_key = self.config.get('openai', {}).get('api_key') or os.getenv('OPENAI_API_KEY')
            if api_key:
                self.clients['openai'] = OpenAI(api_key=api_key)
                logger.info("OpenAI客户端初始化成功")
        except Exception as e:
            logger.warning(f"OpenAI客户端初始化失败: {e}")
        
        # Claude客户端
        try:
            from anthropic import Anthropic
            api_key = self.config.get('claude', {}).get('api_key') or os.getenv('ANTHROPIC_API_KEY')
            if api_key:
                self.clients['claude'] = Anthropic(api_key=api_key)
                logger.info("Claude客户端初始化成功")
        except Exception as e:
            logger.warning(f"Claude客户端初始化失败: {e}")
    
    def get_translation_prompt(self, video_type: str, text: str) -> str:
        """
        根据视频类型生成翻译提示
        """
        type_config = self.video_types_config.get(video_type, {})
        style = type_config.get('translation_style', '准确翻译。')
        
        prompt = f"""You are a professional subtitle translator. Translate each numbered English subtitle into Chinese.
Translation style: {style}

CRITICAL RULES — follow exactly:
1. Output EXACTLY one translated line per input line, numbered to match.
2. Each subtitle is an independent CC block. Even if the English ends mid-sentence (e.g. ends with "the", "a", "in", "to"), translate it AS-IS — do NOT merge it with the next entry.
3. Input has N lines → output must have exactly N lines, same numbering.
4. Output format: "N. Chinese translation" only — no explanations, no extra lines.
5. Keep sound-effect tags like [Music] or [applause] unchanged.

{text}

Translation:"""
        
        return prompt
    
    def translate_with_ollama(self, text: str, video_type: str) -> Optional[str]:
        """使用Ollama翻译"""
        if 'ollama' not in self.clients:
            logger.error("Ollama客户端未初始化")
            return None
        
        try:
            model = self.config.get('ollama', {}).get('model', 'qwen2.5:7b')
            temperature = self.config.get('ollama', {}).get('translation_temperature',
                         self.config.get('ollama', {}).get('temperature', 0.6))
            
            prompt = self.get_translation_prompt(video_type, text)
            
            # 估计输入长度，设置合理的输出限制
            # 中文通常比英文短，留些余量
            estimated_output = len(text) * 2  # 中文token估计
            max_tokens = min(estimated_output, 2000)  # 最多2000 tokens
            
            response = self.clients['ollama'].generate(
                model=model,
                prompt=prompt,
                options={
                    'temperature': temperature,
                    'num_predict': max_tokens,  # 限制输出长度
                    'top_p': 0.9,  # 提高生成速度
                    'top_k': 40,
                }
            )
            
            return response['response'].strip()
        except Exception as e:
            logger.error(f"Ollama翻译失败: {e}")
            return None
    
    def translate_with_openai(self, text: str, video_type: str) -> Optional[str]:
        """使用OpenAI翻译"""
        if 'openai' not in self.clients:
            logger.error("OpenAI客户端未初始化")
            return None
        
        try:
            model = self.config.get('openai', {}).get('model', 'gpt-4')
            temperature = self.config.get('openai', {}).get('translation_temperature',
                         self.config.get('openai', {}).get('temperature', 0.6))
            
            prompt = self.get_translation_prompt(video_type, text)
            
            response = self.clients['openai'].chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature
            )
            
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI翻译失败: {e}")
            return None
    
    def translate_with_claude(self, text: str, video_type: str) -> Optional[str]:
        """使用Claude翻译"""
        if 'claude' not in self.clients:
            logger.error("Claude客户端未初始化")
            return None
        
        try:
            model = self.config.get('claude', {}).get('model', 'claude-3-sonnet-20240229')
            temperature = self.config.get('claude', {}).get('translation_temperature',
                         self.config.get('claude', {}).get('temperature', 0.6))
            
            prompt = self.get_translation_prompt(video_type, text)
            
            response = self.clients['claude'].messages.create(
                model=model,
                max_tokens=1024,
                temperature=temperature,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            return response.content[0].text.strip()
        except Exception as e:
            logger.error(f"Claude翻译失败: {e}")
            return None
    
    def translate_text(self, text: str, video_type: str = "general", provider: Optional[str] = None) -> Optional[str]:
        """
        翻译文本
        """
        provider = provider or self.provider
        
        if provider == 'ollama':
            return self.translate_with_ollama(text, video_type)
        elif provider == 'openai':
            return self.translate_with_openai(text, video_type)
        elif provider == 'claude':
            return self.translate_with_claude(text, video_type)
        else:
            logger.error(f"不支持的提供商: {provider}")
            return None
    
    def translate_srt(self, input_srt: str, output_srt: str, video_type: str = "general",
                     batch_size: int = None) -> bool:
        """
        翻译SRT字幕文件
        batch_size: 批量翻译的字幕数量，默认从config读取，回退到5
        """
        if batch_size is None:
            batch_size = self.config.get('batch_size', 5)
        try:
            subs = pysrt.open(input_srt, encoding='utf-8')
            translated_subs = pysrt.SubRipFile()
            
            total_batches = (len(subs) + batch_size - 1) // batch_size
            logger.info(f"开始翻译字幕，共 {len(subs)} 条，视频类型: {video_type}")
            logger.info(f"批次大小: {batch_size}条/批，总批次: {total_batches}")
            logger.info(f"估计时间: {total_batches * 10 / 60:.1f}-{total_batches * 18 / 60:.1f} 分钟（优化后）")
            
            start_time = time.time()
            
            # 批量处理字幕
            for i in tqdm(range(0, len(subs), batch_size), desc="翻译进度"):
                batch = subs[i:i+batch_size]
                
                # 合并批次中的文本
                batch_texts = [sub.text for sub in batch]
                combined_text = "\n".join([f"{j+1}. {text}" for j, text in enumerate(batch_texts)])
                
                # 翻译合并的文本，不足时以半批重试
                translated_combined = self.translate_text(combined_text, video_type)
                batch_num = i // batch_size + 1

                def _parse_numbered(raw: str) -> Dict:
                    """从LLM输出中提取编号行，返回 {1-based index: text}"""
                    numbered = {}
                    for line in raw.split('\n'):
                        m = re.match(r'^(\d+)\.\s*(.*)', line.strip())
                        if m:
                            num, txt = int(m.group(1)), m.group(2).strip()
                            txt = re.sub(r'^\s*>>\s*', '', txt)
                            txt = re.sub(r'\s*>>\s*', ' ', txt)
                            txt = txt.strip()
                            if txt:
                                numbered[num] = txt
                    return numbered

                if not translated_combined:
                    logger.warning(f"批次 {batch_num} 翻译失败，使用原文")
                    translated_texts = batch_texts
                else:
                    numbered = _parse_numbered(translated_combined)
                    missing = [j + 1 for j in range(len(batch)) if j + 1 not in numbered]

                    # 超过30%缺失时以半批重试
                    if missing and len(missing) > len(batch) * 0.3:
                        logger.warning(
                            f"批次 {batch_num} 仅收到 {len(numbered)}/{len(batch)} 条翻译，"
                            f"缺失 {missing}，以半批重试"
                        )
                        half = max(1, len(batch) // 2)
                        for retry_start in range(0, len(batch), half):
                            retry_slice = list(range(retry_start, min(retry_start + half, len(batch))))
                            retry_texts = [batch_texts[k] for k in retry_slice]
                            retry_combined = "\n".join(
                                [f"{local_j+1}. {t}" for local_j, t in enumerate(retry_texts)]
                            )
                            retry_result = self.translate_text(retry_combined, video_type)
                            if retry_result:
                                retry_numbered = _parse_numbered(retry_result)
                                for local_j, global_j in enumerate(retry_slice):
                                    if global_j + 1 not in numbered and local_j + 1 in retry_numbered:
                                        numbered[global_j + 1] = retry_numbered[local_j + 1]

                    # 按批次位置映射；缺失的条目暂时回退到英文原文
                    translated_texts = [
                        numbered.get(j + 1, batch_texts[j])
                        for j in range(len(batch))
                    ]

                # 逐条验证：结果与英文原文相同说明未翻译，单独重试一次
                for j in range(len(batch)):
                    if translated_texts[j] == batch_texts[j]:
                        single_prompt = f"1. {batch_texts[j]}"
                        single_result = self.translate_text(single_prompt, video_type)
                        if single_result:
                            single_numbered = _parse_numbered(single_result)
                            if 1 in single_numbered:
                                translated_texts[j] = single_numbered[1]
                                logger.debug(f"单条重试成功: 批次{batch_num} 第{j+1}条")

                # 创建翻译后的字幕项
                for j, sub in enumerate(batch):
                    translated_sub = pysrt.SubRipItem(
                        index=len(translated_subs) + 1,
                        start=sub.start,
                        end=sub.end,
                        text=translated_texts[j] if j < len(translated_texts) else sub.text
                    )
                    translated_subs.append(translated_sub)
            
            # 保存翻译后的字幕
            translated_subs.save(output_srt, encoding='utf-8')
            
            elapsed_time = time.time() - start_time
            logger.info(f"✓ 字幕翻译完成: {output_srt}")
            logger.info(f"  总耗时: {elapsed_time / 60:.1f} 分钟")
            logger.info(f"  平均速度: {len(subs) / elapsed_time * 60:.1f} 条/分钟")
            return True
            
        except Exception as e:
            logger.error(f"SRT翻译失败: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # 工业级字幕分割（Caption Segmentation）
    # ──────────────────────────────────────────────────────────────────────────

    def _call_llm_raw(self, prompt: str) -> Optional[str]:
        """
        直接调用当前 LLM 提供商，不包装翻译前缀。
        用于字幕分割等非翻译任务。
        """
        provider = self.provider
        try:
            if provider == 'ollama' and 'ollama' in self.clients:
                model = self.config.get('ollama', {}).get('model', 'qwen3:8b')
                temp  = self.config.get('ollama', {}).get('temperature', 0.3)
                resp  = self.clients['ollama'].generate(
                    model=model, prompt=prompt,
                    options={'temperature': temp, 'num_predict': 1200}
                )
                return resp['response'].strip()

            elif provider == 'openai' and 'openai' in self.clients:
                model = self.config.get('openai', {}).get('model', 'gpt-4')
                temp  = self.config.get('openai', {}).get('temperature', 0.3)
                resp  = self.clients['openai'].chat.completions.create(
                    model=model, temperature=temp,
                    messages=[{"role": "user", "content": prompt}]
                )
                return resp.choices[0].message.content.strip()

            elif provider == 'claude' and 'claude' in self.clients:
                model = self.config.get('claude', {}).get('model', 'claude-3-sonnet-20240229')
                temp  = self.config.get('claude', {}).get('temperature', 0.3)
                resp  = self.clients['claude'].messages.create(
                    model=model, max_tokens=600, temperature=temp,
                    messages=[{"role": "user", "content": prompt}]
                )
                return resp.content[0].text.strip()
        except Exception as e:
            logger.error(f"_call_llm_raw 失败: {e}")
        return None

    def _fallback_groups(self, words: list, max_chars: int = 70) -> list:
        """
        停顿+标点分句（LLM 失败时的 fallback），与 json3_to_srt 逻辑一致。
        返回 [[word_idx, ...], ...]（0-based）。
        """
        LONG_PAUSE_MS = 1500
        PUNCT_GAP_MS  = 300
        MAX_DUR_MS    = 8_000

        groups: list = []
        seg: list = []   # [(local_idx, word_dict), ...]

        def flush():
            if seg:
                groups.append([i for i, _ in seg])
                seg.clear()

        for i, w in enumerate(words):
            if seg:
                prev_ms   = seg[-1][1]['abs_ms']
                gap       = w['abs_ms'] - prev_ms
                dur       = w['abs_ms'] - seg[0][1]['abs_ms']
                tmp       = ''.join(d['word'] for _, d in seg)
                prev_word = seg[-1][1]['word'].rstrip()
                ends_sent = bool(prev_word) and prev_word[-1] in '.!?'
                if (
                    (ends_sent and gap > PUNCT_GAP_MS)
                    or gap > LONG_PAUSE_MS
                    or len(tmp) + len(w['word']) > max_chars
                    or dur > MAX_DUR_MS
                ):
                    flush()
            seg.append((i, w))

        flush()
        return groups

    def segment_captions(self, words: list, video_type: str = 'general') -> list:
        """
        LLM 语义分割（Caption Segmentation）。

        将词级时间流按语义与展示规则分割为字幕单元：
          1. 将词流分批（batch_words 词/批）发送给 LLM
          2. LLM 返回每个字幕单元的首词编号（1-based 全局编号）
          3. 解析编号 → 转换为 0-based 词索引分组
          4. 任意批次失败时自动 fallback 到字符数简单分组

        返回 [[word_idx, ...], ...]（0-based）
        """
        seg_cfg   = self.config.get('caption_segmentation', {})
        max_chars = seg_cfg.get('max_chars_per_line', 70)
        batch_sz  = seg_cfg.get('batch_words', 150)

        # 每个字幕单元约含多少词（用于 prompt 提示）
        avg_word_len = 5  # 英文词平均字符数
        approx_words_per_unit = max(4, max_chars // (avg_word_len + 1))

        all_starts: list[int] = []  # 1-based 全局起始词编号

        for batch_start in range(0, len(words), batch_sz):
            batch = words[batch_start:batch_start + batch_sz]

            # 展示时去除前导空格，避免 LLM 困惑
            word_list_str = '\n'.join(
                f"{batch_start + i + 1}: {w['word'].strip()}"
                for i, w in enumerate(batch)
            )
            first_num = batch_start + 1
            ex_a, ex_b = first_num, first_num + approx_words_per_unit

            prompt = (
                f"You are a professional subtitle segmenter. "
                f"Given the numbered word list below (from a spoken video), "
                f"output the START word number of each subtitle unit.\n\n"
                f"Rules:\n"
                f"1. Each unit must be semantically complete "
                f"(do NOT break inside a noun phrase, verb phrase, or prepositional phrase)\n"
                f"2. Each unit should be roughly {approx_words_per_unit} words "
                f"(max {max_chars} characters when joined)\n"
                f"3. Preferred break points: after sentence-ending punctuation (.!?), "
                f"before conjunctions (and/but/or/so), before a new clause\n"
                f"4. The first unit MUST start at word number {first_num}\n\n"
                f"Words:\n{word_list_str}\n\n"
                f"Output ONLY a comma-separated list of start word numbers. "
                f"Example: {ex_a}, {ex_b}, ...\n"
                f"Output:"
            )

            raw    = self._call_llm_raw(prompt)
            parsed = []
            if raw:
                parsed = [
                    int(m) for m in re.findall(r'\b\d+\b', raw)
                    if batch_start + 1 <= int(m) <= batch_start + len(batch)
                ]

            if len(parsed) >= 2:
                # 校验 LLM 输出合理性：平均 group 字符数应在 [10, max_chars*1.5] 范围内
                avg_group_size = len(batch) / len(parsed)
                avg_chars = avg_group_size * 5  # 平均词长约 5 字符
                if avg_chars < 8 or avg_chars > max_chars * 1.8:
                    logger.warning(
                        f"  批次 {batch_start+1}-{batch_start+len(batch)} LLM 输出异常"
                        f"（avg {avg_chars:.0f} chars/unit），使用 fallback"
                    )
                    fb = self._fallback_groups(
                        words[batch_start:batch_start + len(batch)], max_chars
                    )
                    for g in fb:
                        all_starts.append(batch_start + g[0] + 1)
                else:
                    if parsed[0] != batch_start + 1:
                        parsed.insert(0, batch_start + 1)
                    all_starts.extend(parsed)
                    logger.info(
                        f"  分割批次 {batch_start+1}-{batch_start+len(batch)}: "
                        f"{len(parsed)} 个字幕单元"
                    )
            else:
                logger.warning(
                    f"  批次 {batch_start+1}-{batch_start+len(batch)} 分割失败，"
                    f"使用简单分组"
                )
                fb = self._fallback_groups(
                    words[batch_start:batch_start + len(batch)], max_chars
                )
                for g in fb:
                    all_starts.append(batch_start + g[0] + 1)

        if not all_starts:
            logger.warning("字幕分割全部失败，使用简单分组")
            return self._fallback_groups(words, max_chars)

        # 1-based 全局起始 → 0-based 词索引分组
        starts = sorted(set(all_starts))
        groups = []
        for i, s1 in enumerate(starts):
            s0 = s1 - 1
            e0 = (starts[i + 1] - 1) if i + 1 < len(starts) else len(words)
            groups.append(list(range(s0, e0)))

        logger.info(f"字幕分割完成：{len(words)} 词 → {len(groups)} 条字幕单元")
        return groups

    def validate_breaks_llm(self, words: list, candidates: list) -> dict:
        """
        LLM soft advisor for subtitle break points.

        For each candidate boundary index (word_idx after which to break),
        asks the LLM whether it is semantically OK to start a new subtitle there.
        Returns {word_idx: bool}  True = LLM suggests breaking, False = keep together.

        This is ADVISORY ONLY — the caller applies votes with weight ≤ 0.25.
        Hard blocks (deterministic score == 0.0) are never sent here.
        Called only for ambiguous boundaries (score ∈ [0.30, 0.65]).

        Batched in groups of 20 to keep prompts short.
        """
        if not candidates or not words:
            return {}

        WINDOW = 6   # words of context each side of boundary
        BATCH  = 20

        def _join_w(ws):
            text = ''
            for w in ws:
                wt = w['word']
                if text and not text[-1].isspace() and not wt[0].isspace():
                    text += ' '
                text += wt
            return re.sub(r'\s+', ' ', text).strip()

        all_votes: dict = {}

        for batch_start in range(0, len(candidates), BATCH):
            batch = candidates[batch_start:batch_start + BATCH]
            items = []
            for n, idx in enumerate(batch, 1):
                lo  = max(0, idx - WINDOW + 1)
                hi  = min(len(words), idx + 1 + WINDOW)
                lft = _join_w(words[lo:idx + 1])
                rgt = _join_w(words[idx + 1:hi])
                items.append(f"{n}. «{lft}» | «{rgt}»")

            numbered = '\n'.join(items)
            prompt = (
                "You are reviewing subtitle segmentation for a spoken video.\n"
                "For each numbered break point, «left» is what ends one subtitle "
                "and «right» is what would start the next.\n"
                "Answer Y if it is a GOOD break (right starts a new complete thought) "
                "or N if it is a BAD break (would split a phrase mid-thought).\n\n"
                f"{numbered}\n\n"
                "Reply with ONLY one line per item — number, colon, Y or N. "
                "No explanations.\nExample:\n1: Y\n2: N\n3: Y"
            )

            raw = self._call_llm_raw(prompt)
            if raw:
                for m in re.finditer(r'(\d+)\s*[:.)]\s*([YyNn])', raw):
                    n = int(m.group(1)) - 1
                    if 0 <= n < len(batch):
                        all_votes[batch[n]] = m.group(2).upper() == 'Y'

        logger.info(
            f"validate_breaks_llm: {len(candidates)} 候选 → "
            f"{sum(all_votes.values())} Y / {sum(not v for v in all_votes.values())} N"
        )
        return all_votes
