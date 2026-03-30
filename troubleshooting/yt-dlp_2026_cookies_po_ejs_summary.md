# yt-dlp 2026 阶段性技术总结  
## —— Cookies 与 PO Token（bgutil Provider）问题复盘

---

## 一、背景

自 2025–2026 年起，YouTube 对 **web / mweb / ios** 客户端全面启用了  
**SABR（Server‑Based Adaptive Bitrate） + PO Token（Playback Origin Token）** 机制。

直接后果是：

- ✅ 仅依赖 cookies 的 yt‑dlp **不再能稳定下载视频**
- ❌ 传统“登录即可下载”的模型失效
- ✅ 下载链路被拆分为两个阶段：**登录态** 与 **播放授权**

---

## 二、问题一：Cookies 的使用与隔离

### 2.1 现象

- `--cookies-from-browser` 可正常获取网页和 player API
- 视频下载失败，报错：

```text
Only images are available for download
requires a GVS PO Token which was not provided
```

---

### 2.2 根因

- cookies 仅表示 **登录态**
- 视频播放需要 **PO Token（运行时 JS 生成）**
- PO Token 不存在于 cookies 中

**结论：** cookies 是必要条件，但不是充分条件。

---

### 2.3 正确方案

#### Profile 隔离原则

- 新装 Firefox
- 创建专用 profile（如 `yt_youtube`）
- 只登录新建 Google 账号
- 只访问 YouTube
- cookies 中不应出现公司域 / Okta / SSO

> cookies 数量多少无关紧要，关键看域名。

---

### 2.4 推荐配置

```python
ydl_opts = {
    "cookiesfrombrowser": ("firefox", "yt_youtube", None, None)
}
```

---

## 三、问题二：PO Token 与 bgutil Provider

### 3.1 现象

- cookies 正确
- client 进入（web / mweb）
- video formats 被移除
- 明确提示缺少 PO Token

---

### 3.2 PO Token 本质

- 运行时生成
- 依赖 JavaScript
- 与 session + client 强绑定
- 不可缓存

---

### 3.3 bgutil 架构（关键理解）

```
yt-dlp
  ↑ provider plugin
bgutil Python 包 (pip install)
  ↑ HTTP
Docker PO Server (4416)
```

三者缺一不可。

---

### 3.4 关键踩坑总结

1. **只跑 Docker、不装 Python 包 → 无效**  
2. **指望 yt-dlp 自动发现 HTTP server → 不会发生**  
3. **extractor_args 放在 youtube 下 → 会被忽略**

---

### 3.5 正确 extractor_args 写法

#### 默认端口（无需显式配置）

```python
ydl_opts = {
    'extractor_args': {
        'youtube': {
            'player_client': ['mweb', 'web']
        }
    }
}
```

前提：

```bash
pip install -U bgutil-ytdlp-pot-provider
docker run --rm -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider
docker run --name bgutil-provider --rm -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider
https://github.com/Brainicism/bgutil-ytdlp-pot-provider
```

---

#### 自定义端口 / 地址

```python
ydl_opts = {
    'extractor_args': {
        'youtube': {
            'player_client': ['mweb', 'web']
        },
        'youtubepot-bgutilhttp': {
            'base_url': 'http://127.0.0.1:4416'
        }
    }
}
```

---

## 四、环境注意事项

- Docker 必须使用 `-p` 显式暴露端口
- Windows 可用 `curl http://127.0.0.1:4416` 验证
- WSL 注意 Docker credential / wincred 冲突

---

## 五、最终结论

| 层级 | 作用 | 必需 |
|----|----|----|
| Firefox 专用 profile | 登录态 | ✅ |
| cookiesfrombrowser | 会话输入 | ✅ |
| bgutil Python 插件 | provider | ✅ |
| Docker PO server | Token 计算 | ✅ |
| extractor_args | 启用机制 | ✅ |

---

### 工程判断

- ✅ 技术路线可行
- ❌ 成本和复杂度显著提升
- ✅ 适合实验 / 研究 / 偶用
- ❌ 不适合长期批量下载

---

**阶段性结论：**  
Cookies 与 PO Token 问题已完全厘清并解决，可作为 2026 年 yt-dlp 的稳定参考。

### 附录：EJS（JavaScript Challenge）支持

在 cookies 与 PO Token 均已正确配置的情况下，
YouTube 仍可能通过 JS challenge（n challenge）
阻断视频流格式。

解决方案为启用 yt-dlp 官方支持的 EJS 机制：

- 配置 JS runtime（Node.js）
- 启用 remote EJS solver

示例配置：

```python
'js_runtimes': {'node': {}},
'remote_components': ['ejs:github']
https://github.com/yt-dlp/yt-dlp/wiki/EJS
```