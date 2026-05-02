# B站自动上传模块说明

> 文件位置：`uploader/bilibili_uploader.py`
> 配置文件：`bili_upload.yaml`

---

## 1. 模块架构

```
main.py
  └─ BilibiliUploader (uploader/bilibili_uploader.py)
        ├─ 凭证管理 (_load_credential / _save_credential / login_qrcode)
        ├─ 元数据构建 (_build_meta)
        ├─ 视频上传 (upload → _upload_async)
        │     ├─ VideoUploader (bilibili-api-python)
        │     ├─ 合集添加 (_add_to_season)  ← httpx 直接调用 API
        │     └─ 系列添加 (_add_to_series)  ← bilibili_api.channel_series
        └─ 补充系列 (fix_series)             ← 审核通过后运行
```

### 关键设计：复用 asyncio event loop

bilibili-api 内部通过 `loop.call_soon` / `ensure_future` 调度 chunk 上传任务，必须在**同一个 event loop** 中持续运行。如果每次用 `asyncio.run()` 新建 loop，chunk 任务会报 `coroutine was never awaited`。

```python
def _run_coroutine(self, coro):
    if self._loop is None or self._loop.is_closed():
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
    return self._loop.run_until_complete(coro)
```

所有同步→异步的调用均通过 `_run_coroutine()` 而非 `asyncio.run()`。

---

## 2. 使用流程

### 2.1 首次登录（扫码）

```bash
python main.py --bili-login
```

用 B站 App 扫描终端二维码，凭证自动保存到 `cache/bili_cookies.json`，有效期约半年，到期后重跑登录命令即可。

### 2.2 自动上传待上传视频

```bash
python main.py --upload-only
```

遍历 `cache/processed_videos.json` 中 `upload_status != uploaded` 的视频，逐个上传并更新状态。

### 2.3 手动标记已上传（补录历史）

```bash
python main.py --mark-uploaded <video_id> --bvid BV1xxxxxx
```

### 2.4 过审后补充系列归档

```bash
python main.py --fix-series
```

视频提交后一般需 1–24 小时审核，审核通过后 `aid` 才可被查询到。运行此命令对所有 `upload_status=uploaded` 且 `series_fixed != True` 的视频补充系列。

---

## 3. bili_upload.yaml 配置说明

```yaml
credentials:
  cookie_file: "./cache/bili_cookies.json"  # 登录凭证路径

upload_rules:
  <type>:               # 与 config.yaml 中 video_types 的 key 对应
    tid: 254            # B站分区 ID（见第6节速查表）
    season_id: 8008303  # 合集 ID（null = 不加入合集）
    section_id: 8897999 # 合集内分区 ID（必须抓包获取，见第4节）
    series_id:          # 系列 ID（标量或 {channel_id: sid} 映射）
      "@handle": 5116054
      _default: null    # 没有匹配的 channel_id 时使用（null=跳过）
    tags: ["少儿英语"]
    title_template: "{title}"         # 支持 {title} {youtube_url} {channel}
    desc_template: |
      英文原视频：{youtube_url}
    is_reprint: true    # 转载视频需填 true，原创填 false

behavior:
  upload_interval_secs: 30   # 两次上传之间的等待秒数
  max_retries: 3             # 上传失败最大重试次数
  retry_delay_secs: 60       # 重试间隔（秒）
```

### 3.1 series_id 两种格式

**标量**（所有视频加入同一系列）：
```yaml
series_id: 5116045
```

**映射**（按频道 channel_id 路由）：
```yaml
series_id:
  "@SuperSimpleSongs": 5116051
  "@msrachel":         5116054
  _default: null       # 不匹配时跳过，避免加到错误系列
```

`channel_id` 来自 `channels.yaml` 中扫描时存入 cache 的字段，格式为 YouTube `@handle`（例如 `@msrachel`）。若旧版 cache 缺少该字段，可手动在 `cache/processed_videos.json` 对应条目补充。

---

## 4. 合集 section_id 获取方法（抓包）

B站合集（ugc_season）结构：

```
season（合集，URL可见，如 /channel/collectiondetail?sid=8008303）
  └─ section（分区，通常只有一个"正片"分区，ID不在创作中心界面显示）
       └─ episode（单集视频）
```

添加集数需要提供 `sectionId`，只能通过抓包获取，步骤如下：

1. 浏览器打开 B站创作中心 → 合集列表 → 进入目标合集编辑页
2. 打开开发者工具（F12）→ Network 标签
3. 筛选 XHR / Fetch 请求，搜索 `section`
4. 找到类似 `GET /x2/creative/web/season/sections?seasonId=XXXXXXX` 的请求
5. 查看响应 JSON：

```json
{
  "data": {
    "sections": [
      {
        "id": 8897999,        ← 这就是 section_id
        "title": "正片",
        "type": 1
      }
    ]
  }
}
```

6. 将 `section_id` 填入 `bili_upload.yaml` 对应规则的 `section_id` 字段

> **注意**：`section_id` 与 `season_id` 是两个不同的 ID，前者不在创作中心 URL 中显示，必须抓包。

### 合集添加 API

上传成功后，程序通过 `_add_to_season()` 方法直接调用 member.bilibili.com API：

```
POST https://member.bilibili.com/x2/creative/web/season/section/episodes/add
params: t=<timestamp>&csrf=<bili_jct>
body:   {"sectionId": 8897999, "episodes": [{"title": "...", "cid": 12345, "aid": 67890}], "csrf": "..."}
```

- `cid`（视频 cid）从 `PRE_SUBMIT` 上传事件中捕获
- `aid` 从 `uploader.start()` 返回值中获取

---

## 5. 系列（channel_series）添加原理

B站系列（旧版合集）与合集（ugc_season）是两套独立系统：

| | 合集（ugc_season）| 系列（channel_series）|
|---|---|---|
| 创建位置 | 创作中心 → 合集管理 | 个人主页 → 频道 → 系列 |
| 添加时机 | 上传时可携带 | 视频**过审后**才可通过 API 添加 |
| 对外可见 | 独立页面 URL | 个人主页频道 |

### 为什么需要 --fix-series

视频投稿后进入审核队列，审核中的视频 `aid` 无法被 `video.get_info()` 查到（返回 -404）。因此系列添加分两步：

1. 上传时只做合集添加（`_add_to_season`，上传后立即可执行）
2. 次日过审后运行 `--fix-series`，批量调用 `add_aids_to_series`

---

## 6. B站分区 tid 速查表

> 本项目所用分区（2025年验证有效）

| tid | 路径 | 用途 |
|-----|------|------|
| 254 | 生活 > 亲子 | baby 类型视频 |
| 231 | 科技 > 计算机技术 | tech / interview / zh 类型 |

> **注意**：B站投稿 API 中**没有独立的"人工智能"叶子分区**，AI 相关视频用 `231`（计算机技术）。
> `tid=200` 是"国风舞蹈"，`tid=188` 是父分区，均不可用于投稿。

完整分区 ID 参考：https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/video/video_zone.md

---

## 7. 常见问题排查

### Q: 上传卡住不动（hang）

**原因 1**：封面为空字符串 `cover=''`  
`VideoUploader` 遇到空封面会静默等待 60 秒然后重试，无任何提示。

**解决**：`_extract_cover()` 使用 ffmpeg 截帧，失败时用 PIL 生成纯黑 JPG 占位图，确保始终返回有效路径。

**原因 2**：网络问题  
增加详细日志，观察 `PREUPLOAD / PRE_CHUNK / AFTER_CHUNK` 事件是否正常推进。

---

### Q: RuntimeWarning: coroutine was never awaited

**原因**：在 `upload()` 的每次调用中使用了 `asyncio.run()`（每次新建 loop），而 bilibili-api 的 chunk 上传任务是在旧 loop 上调度的。

**解决**：使用 `_run_coroutine()` 复用同一个 event loop 实例（见第1节）。

---

### Q: 加入合集失败，返回 {"code": -404}

**可能原因**：
1. `section_id` 填写错误（常见：填了 `season_id` 当作 `section_id`）
2. `section_id` 对应的 section 不属于该 `season_id`

**解决**：重新抓包确认 `sectionId`（见第4节），注意 season_id 和 section_id 是完全不同的两个 ID。

---

### Q: --fix-series 执行后部分视频未添加到系列

**常见原因**：`cache/processed_videos.json` 中该视频的 `channel_id` 字段为 `null` 或与 `series_id` 映射中的 key 不匹配。

**解决**：
1. 检查该视频的 channel_id：
   ```bash
   python -c "
   import json
   with open('./cache/processed_videos.json', encoding='utf-8') as f:
       c = json.load(f)
   for vid, v in c.items():
       m = v.get('metadata', {})
       if m.get('upload_status') == 'uploaded':
           print(vid, m.get('channel_id'), m.get('series_fixed'))
   "
   ```
2. 手动在 cache JSON 中补充 `channel_id`（格式：`"@handle"`），再重跑 `--fix-series`

---

### Q: 分区（tid）填写错误导致视频发布到错误分类

审核通过后在创作中心编辑视频，手动修改分区。下次上传前核对 `bili_upload.yaml` 中的 `tid` 值。

---

## 8. 当前合集/系列配置（截至 2026-05）

| 类型 | season_id | section_id | 合集名称 |
|------|-----------|------------|----------|
| baby | 8008303 | 8897999 | 婴幼儿英语启蒙Youtube双字幕视频 |
| tech | 8032035 | 8924619 | Youtube人工智能信息技术分享 |
| interview | 8032035 | 8924619 | （与 tech 共享） |
| zh | 8033403 | *(待填)* | *(建立后抓包获取)* |

| 频道 | series_id |
|------|-----------|
| @SuperSimpleSongs | 5116051 |
| @SuperSimplePlay | 5116049 |
| @msrachel | 5116054 |
| @matthew_berman / @AIDailyBrief / @mreflow / tech默认 | 5116045 |
| @HungyiLeeNTU / zh默认 | 5116053 |
| python 系列 | 5116048 |
