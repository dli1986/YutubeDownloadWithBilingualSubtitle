# YouTube双语字幕生成系统

一个强大的自动化工具，用于下载YouTube视频并生成高质量的中英双语字幕。支持YouTube和YouTube Kids平台，使用Whisper ASR和LLM技术，根据视频类型智能调整翻译风格。

## 📋 目录

- [功能特性](#功能特性)
- [技术栈](#技术栈)
- [系统架构](#系统架构)
- [安装部署](#安装部署)
- [配置说明](#配置说明)
- [使用方法](#使用方法)
- [技术方案](#技术方案)
- [常见问题](#常见问题)
- [开发说明](#开发说明)

## ✨ 功能特性

### 核心功能

1. **智能视频下载**
   - 支持YouTube和YouTube Kids平台
   - 自动延迟机制，突破SABR速率限制
   - 支持代理和Cookie认证
   - 自动重试和错误恢复

2. **高质量字幕生成**
   - 使用OpenAI Whisper进行音频转录
   - 支持多种LLM提供商（Ollama、OpenAI、Claude）
   - 根据视频类型智能调整翻译风格
   - 精确的时间轴对齐

3. **视频类型识别**
   - **婴幼儿视频**：亲切温柔、简单易懂的语言
   - **科技分享**：准确专业的技术术语
   - **访谈节目**：轻松口语化的表达
   - **纪录片**：标准流畅的书面语

4. **智能缓存管理**
   - 记录已处理视频，避免重复下载
   - 失败重试机制
   - 可手动清除缓存重新处理

5. **灵活的输出选项**
   - 生成独立的SRT字幕文件
   - 可选择嵌入硬字幕到视频
   - 支持软字幕附加

## 🛠️ 技术栈

### 核心技术

- **Python 3.8+**：主要开发语言
- **yt-dlp**：YouTube视频下载
- **OpenAI Whisper**：语音识别和转录
- **Ollama/OpenAI/Claude**：大语言模型翻译
- **FFmpeg**：视频处理和字幕嵌入
- **pysrt**：字幕文件处理

### 依赖库

```
yt-dlp>=2024.0.0          # YouTube下载
whisper>=1.0.0            # 语音识别
openai-whisper>=20231117  # Whisper模型
pysrt>=1.1.2              # 字幕处理
ffmpeg-python>=0.2.0      # 视频处理
ollama>=0.1.0             # 本地LLM
openai>=1.0.0             # OpenAI API
anthropic>=0.18.0         # Claude API
pyyaml>=6.0               # 配置文件
python-dotenv>=1.0.0      # 环境变量
tqdm>=4.65.0              # 进度条
```

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                         主程序 (main.py)                      │
│                    流程编排 + 错误处理                         │
└────────────┬────────────────────────────────────────────────┘
             │
    ┌────────┴────────┬─────────────┬──────────────┬──────────┐
    │                 │             │              │          │
┌───▼────┐    ┌──────▼──────┐  ┌──▼─────┐   ┌────▼────┐  ┌──▼──────┐
│下载模块 │    │转录模块      │  │翻译模块 │   │合并模块  │  │视频处理  │
│Downloader│   │Transcriber  │  │Translator│  │Merger   │  │Processor│
└───┬────┘    └──────┬──────┘  └──┬─────┘   └────┬────┘  └──┬──────┘
    │                │             │              │          │
    │  yt-dlp        │  Whisper    │  LLM API     │  pysrt   │  ffmpeg
    └────────────────┴─────────────┴──────────────┴──────────┘
                            │
                    ┌───────▼────────┐
                    │  缓存管理模块    │
                    │  Cache Manager  │
                    └────────────────┘
```

### 模块说明

1. **downloader.py** - 视频下载模块
   - 使用yt-dlp下载视频和原始字幕
   - 实现延迟机制避免SABR限制
   - 支持代理和认证

2. **transcriber.py** - Whisper转录模块
   - 加载和管理Whisper模型
   - 音频转文字转录
   - VTT/SRT格式转换

3. **translator.py** - LLM翻译模块
   - 多LLM提供商支持
   - 视频类型识别和风格调整
   - 批量翻译优化

4. **subtitle_merger.py** - 字幕合并模块
   - 时间轴对齐算法
   - 双语字幕生成
   - 字幕清理和优化

5. **video_processor.py** - 视频处理模块
   - FFmpeg封装
   - 硬/软字幕嵌入
   - 视频重封装和修复

6. **cache_manager.py** - 缓存管理模块
   - 已处理视频跟踪
   - 失败记录和重试
   - 统计信息

7. **utils.py** - 工具函数模块
   - 配置加载
   - 日志设置
   - 通用辅助函数

## 📦 安装部署

### 1. 环境要求

- Python 3.8 或更高版本
- FFmpeg（用于视频处理）
- 至少4GB RAM（运行Whisper medium模型）
- GPU可选（加速Whisper转录）

### 2. 安装FFmpeg

**Windows:**
```powershell
# 使用Chocolatey
choco install ffmpeg

# 或手动下载并添加到PATH
# https://ffmpeg.org/download.html
```

**Linux:**
```bash
sudo apt update
sudo apt install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

### 3. 安装Python依赖

```bash
cd Subtitle
pip install -r requirements.txt
```

### 4. 启动 bgutil PO Token 服务（必需）

YouTube 当前所有客户端均需要 GVS PO Token 才能正常下载。本项目通过 [bgutil-ytdlp-pot-provider](https://github.com/coletdjnz/yt-dlp-youtube-oauth2) 插件和 Docker 服务自动获取 Token。

**步骤一：在 WSL 或 Linux 中启动 Docker 服务**

```bash
docker run -d \
  --name bgutil-pot \
  -p 4416:4416 \
  --restart unless-stopped \
  ghcr.io/nickovs/bgutil-ytdlp-pot-provider:latest
```

服务启动后监听 `127.0.0.1:4416`，yt-dlp 插件会自动连接，无需额外配置。

**步骤二：安装 yt-dlp 插件**

```bash
pip install bgutil-ytdlp-pot-provider
```

验证插件加载成功：

```bash
yt-dlp --version   # 运行时日志应显示 bgutil:http-x.x.x (external)
```

> **注意**：每次重启机器后需确认 Docker 服务正在运行（`docker ps`），否则下载时会因无法获取 PO Token 而失败。

### 5. 安装Ollama（可选，用于本地LLM）

访问 [Ollama官网](https://ollama.com/) 下载并安装，然后下载模型：

```bash
ollama pull qwen2.5:7b
```

### 5. 配置API密钥（可选）

如果使用OpenAI或Claude，创建`.env`文件：

```bash
OPENAI_API_KEY=your_openai_api_key
ANTHROPIC_API_KEY=your_claude_api_key
```

## ⚙️ 配置说明

编辑 `config.yaml` 文件进行配置：

### 下载配置

```yaml
downloader:
  download_delay: 300      # 下载延迟（秒），避免SABR限制
  max_retries: 3           # 最大重试次数
  video_quality: "best"    # 视频质量
  proxy: null              # 代理设置（可选）
  cookies_file: null       # Cookie文件（可选）
```

### Whisper配置

```yaml
transcriber:
  model: "medium"          # 模型大小：tiny/base/small/medium/large
  device: "cpu"            # 设备：cpu/cuda
  language: "en"           # 语言代码
  fp16: false              # 是否使用FP16（需要GPU）
```

### LLM配置

```yaml
translator:
  default_provider: "ollama"  # 默认提供商：ollama/openai/claude
  
  ollama:
    host: "http://localhost:11434"
    model: "qwen2.5:7b"
    temperature: 0.3
  
  openai:
    model: "gpt-4"
    temperature: 0.3
  
  claude:
    model: "claude-3-sonnet-20240229"
    temperature: 0.3
```

### 视频类型配置

视频类型由 `videos.txt` 中 URL 后的标注**人工指定**，类型名同时决定输出目录结构（`output/<类型>/`）。`config.yaml` 中每个类型只需配置翻译风格，新增类型无需修改任何代码：

```yaml
video_types:
  baby:
    translation_style: "使用简单、亲切、充满童趣的语言..."
  
  tech:
    translation_style: "使用准确、专业的技术术语..."
  
  # 新增类型只需追加 key + translation_style
  # sports:
  #   translation_style: "使用运动相关术语，语言简洁有力..."
```

## 🚀 使用方法

### 方式0：频道订阅（自动追踪新视频）

编辑 `channels.yaml`，填入要订阅的 YouTube 频道：

```yaml
channels:
  - id: "UCxxxxxxxxxxxxxx"      # UC 开头的 channel ID
    type: "tech"
    min_duration_mins: 20       # 过滤短视频/预告片

  - id: "@3blue1brown"          # 或 @handle 格式
    type: "documentary"
    min_duration_mins: 15

  - id: "UCyyyyyyyyyyyyyy"
    type: "zh"                  # 中文源频道走快速路径
    min_duration_mins: 20
```

每次运行 `python main.py` 时，程序会先自动扫描这些频道，把新视频加入处理队列。

#### 增量策略说明

```
首次订阅某频道（cache/ 下无对应 .archive 文件）
  → yt-dlp 拉取该频道全部历史视频 ID，写入 archive，但 不入队处理
  → 效果：以"今天"为追踪起点，不会处理堆积的历史内容

后续每次运行
  → yt-dlp 读取 archive，跳过已有 ID，只返回新发布的视频
  → 新视频 ID 立即写回 archive（防止下次重复入队，即使本次处理失败）
  → 新视频加入处理队列，走完整下载→转录→翻译→嵌入流程
```

> **为什么扫 200 条？**
> yt-dlp 只拉取元数据（不下载视频），速度极快（通常 < 3 秒/频道）。
> 正常频道每周发布 1-5 条，200 条足以覆盖数月积累，同时避免翻遍全部历史。
> 因为有 archive 过滤，实际返回的新视频条数通常是 0-5 条。

#### 文件说明

| 文件 | 用途 |
|------|------|
| `channels.yaml` | 频道订阅列表（人工维护） |
| `cache/channel_<id>.archive` | 每个频道独立的已处理 ID 档案 |
| `videos.txt` | 一次性手动补充（仍完全有效） |

`videos.txt` 与 `channels.yaml` 并存：频道订阅负责自动追踪，`videos.txt` 用于手动添加单条视频（如特定历史内容）。

### 方式1：批量处理（推荐）

1. 编辑 `videos.txt` 添加视频URL：

```txt
https://www.youtube.com/watch?v=xxxxx baby 儿童学习视频
https://www.youtube.com/watch?v=yyyyy tech Python教程
https://www.youtube.com/watch?v=zzzzz interview 技术访谈
```

2. 运行主程序：

```bash
python main.py
```

### 方式2：处理单个视频

```bash
python main.py --url "https://www.youtube.com/watch?v=xxxxx" --type baby
```

### 命令行参数

```bash
python main.py --help

参数:
  --config CONFIG              配置文件路径（默认：./config.yaml）
  --videos VIDEOS              视频列表文件路径（默认：./videos.txt）
  --url URL                    处理单个视频URL
  --type TYPE                  视频类型（对应 config.yaml 中 video_types 的 key，如 baby/tech/interview）
  --embed-only VIDEO_ID        仅重新嵌入字幕，跳过下载/转录/翻译
                               用途：ffmpeg参数调整后重新生成视频，不重新处理字幕
  --reprocess-subtitle VIDEO_ID  重新处理字幕流程（VTT→SRT→翻译→合并→嵌入），跳过视频下载
                               用途：修复字幕质量问题、更换翻译模型、调整翻译风格后重新翻译
```

### 分段处理示例

```bash
# 完整流程
python main.py --url "https://www.youtube.com/watch?v=xxxxx" --type interview

# 只重新翻译+合并+嵌入（视频已下载，修复字幕内容问题）
python main.py --reprocess-subtitle VIDEO_ID

# 只重新嵌入（字幕已就绪，修复视频编码/路径问题）
python main.py --embed-only VIDEO_ID
```

### 输出结构

```
output/
└── <类型>/                      # 由 videos.txt 中标注的类型决定
    └── VIDEO_ID/
        ├── VIDEO_TITLE.bilingual.srt    # 双语字幕文件
        └── VIDEO_TITLE.bilingual.mp4    # 嵌入字幕的视频（可选）

cache/
└── VIDEO_ID/
    ├── VIDEO_ID.mp4                 # 原始视频
    ├── VIDEO_ID.en.vtt              # YouTube 原始字幕（VTT）
    ├── subtitle.en.srt              # 转换后的英文字幕
    └── subtitle.zh.srt              # 中文翻译字幕
```

## 🔧 技术方案

### 1. 突破YouTube SABR限制

**问题**：YouTube的SABR（Streaming API Bandwidth Restriction）机制限制连续下载字幕。

**解决方案**：
- 在每次下载之间添加可配置的延迟（默认300秒）
- 使用yt-dlp的Cookie认证绕过部分限制
- 支持代理轮换
- 实现智能重试机制

```python
def _wait_if_needed(self):
    """等待必要的时间间隔，避免触发SABR限制"""
    current_time = time.time()
    time_since_last = current_time - self.last_download_time
    
    if time_since_last < self.download_delay:
        wait_time = self.download_delay - time_since_last
        logger.info(f"等待 {wait_time:.0f} 秒以避免速率限制...")
        time.sleep(wait_time)
```

### 2. 智能字幕获取策略

**问题**：如何高效获取英文字幕？Whisper转录虽然准确但耗时长且消耗GPU。

**解决方案 - 两步策略**：

#### 步骤1：优先使用YouTube原始字幕
```python
# 1. 尝试下载YouTube原始字幕（包括人工字幕和自动生成CC）
# 2. 检查字幕类型（人工 or 自动）
# 3. 如果存在，直接使用，跳过Whisper转录
```

**优势**：
- ⚡ 极快 - 几秒钟即可获得
- 💰 省钱 - 不消耗GPU资源
- 📝 通常质量良好 - 特别是人工字幕

#### 步骤2：Whisper转录作为备选
```python
# 仅当没有原始字幕时才启用
if not downloaded_subtitle:
    # 使用Whisper ASR转录
    # 准确度高，但需要时间
```

**适用场景**：
- 视频没有任何字幕
- 需要最高准确度
- 处理专业术语较多的内容

**性能对比**：
| 方法 | 时间（1小时视频） | GPU占用 | 准确度 |
|------|-----------------|---------|--------|
| 原始字幕 | ~5秒 | 0% | 良好-优秀 |
| Whisper large-v3 | ~3-8分钟 | ~6GB | 优秀 |

**实现代码**：
```python
def _get_or_create_english_subtitle(self, video_id, video_path, downloaded_subtitle):
    # 优先使用下载的字幕
    if downloaded_subtitle and os.path.exists(downloaded_subtitle):
        logger.info("✓ 发现YouTube原始字幕，跳过Whisper转录")
        return convert_to_srt(downloaded_subtitle)
    
    # 备选方案：Whisper转录
    logger.info("✗ 未找到原始字幕，使用Whisper转录...")
    return whisper_transcribe(video_path)
```

### 3. 时间轴对齐算法

**挑战**：中英文字幕的时间轴可能不完全匹配。

**解决方案**：
- 计算时间重叠度
- 查找最佳匹配片段
- 使用阈值过滤低质量匹配

```python
def _calculate_overlap(self, sub1, sub2):
    """计算两个字幕的时间重叠度"""
    overlap_start = max(sub1.start.ordinal, sub2.start.ordinal)
    overlap_end = min(sub1.end.ordinal, sub2.end.ordinal)
    return max(0, overlap_end - overlap_start)
```

### 3. 时间轴对齐算法

**挑战**：中英文字幕的时间轴可能不完全匹配。

**解决方案**：
- 计算时间重叠度
- 查找最佳匹配片段
- 使用阈值过滤低质量匹配

```python
def _calculate_overlap(self, sub1, sub2):
    """计算两个字幕的时间重叠度"""
    overlap_start = max(sub1.start.ordinal, sub2.start.ordinal)
    overlap_end = min(sub1.end.ordinal, sub2.end.ordinal)
    return max(0, overlap_end - overlap_start)
```

### 4. 智能翻译风格调整

**目标**：根据视频类型提供合适的翻译风格。

**实现**：
- 关键词匹配识别视频类型
- 为每种类型定制翻译提示词
- 支持用户自定义风格

```python
# 婴儿视频 - 亲切温柔
translation_style: "使用简单、亲切、充满童趣的语言，适合婴幼儿..."

# 技术视频 - 准确专业
translation_style: "使用准确、专业的技术术语，保持严谨性..."

# 访谈视频 - 轻松口语
translation_style: "使用轻松、口语化的表达，保持对话的自然流畅感..."
### 4. 智能翻译风格调整

**目标**：根据视频类型提供合适的翻译风格。

**实现**：
- 关键词匹配识别视频类型
- 为每种类型定制翻译提示词
- 支持用户自定义风格

```python
# 婴儿视频 - 亲切温柔
translation_style: "使用简单、亲切、充满童趣的语言，适合婴幼儿..."

# 技术视频 - 准确专业
translation_style: "使用准确、专业的技术术语，保持严谨性..."

# 访谈视频 - 轻松口语
translation_style: "使用轻松、口语化的表达，保持对话的自然流畅感..."
```

### 5. 批量翻译优化

**问题**：逐条翻译字幕效率低、API调用次数多。

**优化**：
- 批量合并字幕进行翻译（默认5条一批）
- 减少API调用次数和成本
- 保持上下文连贯性

```python
# 合并批次中的文本
batch_texts = [sub.text for sub in batch]
combined_text = "\n".join([f"{j+1}. {text}" for j, text in enumerate(batch_texts)])

# 一次性翻译
translated_combined = self.translate_text(combined_text, video_type)
```

## ❓ 常见问题

### Q1: 下载速度慢或被限制？

**A:** 
- 增加 `download_delay` 配置（建议300-600秒）
- 使用代理服务器
- 使用YouTube账号Cookie认证
- 避免高峰时段下载

### Q2: Whisper转录速度慢？

**A:**
- 使用更小的模型（如`small`或`base`）
- 使用GPU加速（设置`device: cuda`）
- 优先使用YouTube原始字幕

### Q3: 翻译质量不理想？

**A:**
- 调整 `translation_style` 描述
- 尝试不同的LLM提供商
- 降低 `temperature` 参数获得更稳定输出
- 增加批量翻译的batch_size保持上下文

### Q4: FFmpeg命令失败？

**A:**
- 确认FFmpeg已正确安装：`ffmpeg -version`
- Windows用户注意路径转义
- 检查视频文件是否损坏
- 尝试使用 `remux_video` 重新封装

### Q5: 内存不足？

**A:**
- 使用更小的Whisper模型
- 处理视频前先下载字幕
- 分批处理视频列表
- 增加系统虚拟内存

## 👨‍💻 开发说明

### 项目结构

```
Subtitle/
├── main.py                 # 主程序入口
├── config.yaml            # 配置文件
├── requirements.txt       # Python依赖
├── videos.txt             # 视频URL列表
│
├── downloader.py          # 视频下载模块
├── transcriber.py         # Whisper转录模块
├── translator.py          # LLM翻译模块
├── subtitle_merger.py     # 字幕合并模块
├── video_processor.py     # 视频处理模块
├── cache_manager.py       # 缓存管理模块
├── utils.py               # 工具函数模块
├── bilingual_merge.py     # 原始字幕合并脚本
│
├── cache/                 # 缓存目录
│   ├── processed_videos.json  # 处理记录
│   └── VIDEO_ID/          # 各视频的缓存文件
│
├── output/                # 输出目录
│   └── VIDEO_ID/          # 各视频的输出文件
│
└── logs/                  # 日志目录
    └── subtitle_generator.log
```

### 添加新的LLM提供商

1. 在 `translator.py` 的 `_init_clients` 中添加客户端初始化
2. 实现 `translate_with_newprovider` 方法
3. 在 `translate_text` 中添加分支
4. 更新 `config.yaml` 添加配置项

### 自定义视频类型

在 `config.yaml` 中添加新类型：

```yaml
video_types:
  custom_type:
    keywords: ["keyword1", "keyword2"]
    translation_style: "你的翻译风格描述..."
```

### 扩展功能建议

- [ ] Web界面（Streamlit/Flask）
- [ ] 多语言支持（不仅限于中英）
- [ ] 字幕样式定制（字体、颜色、位置）
- [ ] 视频剪辑功能
- [ ] 云端部署支持
- [ ] 批量下载优化（并行处理）

## 📝 许可证

本项目仅供学习和研究使用。使用本工具下载内容时，请遵守YouTube的服务条款和版权法律。

## 🙏 致谢

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - 强大的YouTube下载工具
- [OpenAI Whisper](https://github.com/openai/whisper) - 优秀的语音识别模型
- [Ollama](https://ollama.com/) - 本地LLM运行环境
- [FFmpeg](https://ffmpeg.org/) - 视频处理瑞士军刀

## 📧 联系方式

如有问题或建议，请提交Issue或Pull Request。

---

**Happy Subtitle Generating! 🎬🎯**
