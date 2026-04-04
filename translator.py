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
        
        prompt = f"""你是专业字幕翻译员，将英文字幕逐条翻译成中文。
翻译风格：{style}

规则：
- 每一条必须给出对应编号的翻译，不能跳过任何一条
- 相邻条目内容可能有重叠（滚动字幕），仍需全部独立翻译
- 只输出"编号. 中文翻译"格式，不加任何解释
- 纯音效标签如[Music]保留原样

{text}

翻译："""
        
        return prompt
    
    def translate_with_ollama(self, text: str, video_type: str) -> Optional[str]:
        """使用Ollama翻译"""
        if 'ollama' not in self.clients:
            logger.error("Ollama客户端未初始化")
            return None
        
        try:
            model = self.config.get('ollama', {}).get('model', 'qwen2.5:7b')
            temperature = self.config.get('ollama', {}).get('temperature', 0.3)
            
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
            temperature = self.config.get('openai', {}).get('temperature', 0.3)
            
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
            temperature = self.config.get('claude', {}).get('temperature', 0.3)
            
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

                    still_missing = [j + 1 for j in range(len(batch)) if j + 1 not in numbered]
                    if still_missing:
                        logger.warning(f"批次 {batch_num} 仍有 {len(still_missing)} 条未翻译，回退英文原文")

                    # 按批次位置映射；缺失的条目回退到英文原文
                    translated_texts = [
                        numbered.get(j + 1, batch_texts[j])
                        for j in range(len(batch))
                    ]
                
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
