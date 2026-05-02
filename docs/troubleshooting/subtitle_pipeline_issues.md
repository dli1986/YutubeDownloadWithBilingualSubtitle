# Subtitle Pipeline Troubleshooting Log

记录开发过程中遇到的实际问题及修复方案，便于回溯和复用。

---

## 1. 音频无声 / 播放器不支持

**现象**
输出视频在 Windows 默认播放器（Movies & TV、Media Player）中打开后没有声音。

**根本原因**
ffmpeg 嵌入字幕时使用 `-c:a copy`，原始音轨为 **Opus** 编码（yt-dlp 下载 AV1+Opus 的 WebM/MP4 封装）。Windows 内置播放器不支持 Opus，静默失败。

**修复**
`video_processor.py` → `embed_subtitle()`：
```python
# Before
'-c:a', 'copy'

# After
'-c:a', 'aac', '-b:a', '192k'
```

---

## 2. 字幕显示 HTML 实体（`&gt;&gt;` → `>>`）

**现象**
字幕中出现原始 HTML 转义字符，例如 `&gt;&gt; Huh? &gt;&gt; Aha!`。

**根本原因**
YouTube VTT 字幕用 HTML 实体编码特殊字符。旧版 `vtt_to_srt()` 直接用 `pysrt.open()` 解析，未做 HTML 反转义。

**修复**
重写 `transcriber.py` → `vtt_to_srt()`，手动解析 VTT 块，添加：
```python
text = html.unescape(text)   # &gt; → >  &amp; → &  &lt; → <  等
```

---

## 3. 字幕显示 YouTube Speaker-Change 标记（`>>`）

**现象**
字幕中出现 `>> Huh? >> Aha!` 或 `[Applause] >> So cold.`，`>>` 为 YouTube 自动字幕的说话人切换标记。

**根本原因**
HTML 实体 `&gt;&gt;` 经 `html.unescape()` 还原为 `>>`，但未进一步过滤；LLM 翻译时也可能将英文源中的 `>>` 复制到中文输出。

**修复（两处）**

`transcriber.py` → `vtt_to_srt()`（源头清理）：
```python
text = re.sub(r'^\s*>>\s*', '', text)    # 行首 >> 直接删除
text = re.sub(r'\s*>>\s*', ' ', text)    # 行内 >> 替换为空格
```

`translator.py` → `translate_srt()`（防御层，防 LLM 复现）：
```python
text = re.sub(r'^\s*>>\s*', '', text)
text = re.sub(r'\s*>>\s*', ' ', text)
```

---

## 4. 字幕显示 VTT 内联时间标签

**现象**
字幕文本中出现 `<00:00:04.240><c>play</c>` 等 VTT karaoke 标签。

**根本原因**
VTT 格式支持逐词高亮（karaoke），yt-dlp 下载的自动生成字幕包含这些标签，旧版 `vtt_to_srt()` 未清除。

**修复**
`transcriber.py` → `vtt_to_srt()`：
```python
text = re.sub(r'<\d{2}:\d{2}[:.\d]*>', '', text)   # 内联时间戳
text = re.sub(r'</?c>', '', text)                   # karaoke 标签
text = re.sub(r'<[^>]+>', '', text)                 # 其余 HTML/VTT 标签兜底
```

---

## 5. LLM 翻译结果保留数字编号前缀

**现象**
中文字幕中出现 `2.[音乐][掌声]`、`4.[掌声]>>好冷。` 等，行首数字编号未被剥离。

**根本原因**
批量翻译时将多条字幕拼接为 `1. text\n2. text\n...` 送入 LLM，解析返回结果时依赖：
```python
if '. ' in line:
    text = line.split('. ', 1)[1]
```
当 LLM 返回 `2.[音乐]`（点号后无空格）时，`'. '` 匹配失败，整行含编号原样保留。

**修复**
`translator.py` → `translate_srt()`，改用正则：
```python
# Before
if '. ' in line:
    text = line.split('. ', 1)[1] if len(line.split('. ', 1)) > 1 else line

# After
text = re.sub(r'^\d+\.\s*', '', line)   # 匹配 "1. " 也匹配 "2.[" 无空格场景
```

---

## 6. ffmpeg libass 字幕路径解析失败（Windows）

**现象**
ffmpeg 报 `Invalid argument` 或 filter 解析错误，字幕嵌入失败。

**根本原因（两个）**
1. 文件名含 `+` 等特殊字符，libass subtitles filter 解析路径时出错。
2. Windows 路径含驱动器盘符冒号（`C:`），libass 将其误判为 filter 选项分隔符。

**修复**
`video_processor.py` → `embed_subtitle()`：
- 使用仅含 `video_id` 的临时文件名（无特殊字符），完成后再 `os.replace()` 重命名。
- 通过 `subprocess.Popen(cwd=output_dir)` 将工作目录设为输出目录，subtitles filter 中只传文件名（无路径），规避盘符冒号问题：
```python
'-vf', f"subtitles='{srt_filename}'"   # 只有文件名，无绝对路径
```

---

## 通用排查流程

1. 字幕质量问题 → 先检查 `cache/VIDEO_ID/VIDEO_ID.en.vtt` 原始内容
2. 翻译质量问题 → 检查 `cache/VIDEO_ID/subtitle.en.srt`（干净英文源）
3. 重新处理字幕（不重新下载）：
   ```
   python main.py --reprocess-subtitle VIDEO_ID
   ```
4. 仅重新嵌入视频：
   ```
   python main.py --embed-only VIDEO_ID
   ```
