# 修复 403 Forbidden 错误

## 问题现象

```
[download]   1.7% of  572.90MiB at    5.44MiB/s ETA 01:43
ERROR: unable to download video data: HTTP Error 403: Forbidden
```

**好消息**：格式选择已正确（399+251，1080p，572MB）  
**问题**：YouTube检测到下载行为并阻止访问

## 已应用的修复

### ✅ 1. 增强浏览器伪装

添加了真实浏览器的HTTP头：
```python
'http_headers': {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...',
    'Accept': 'text/html,application/xhtml+xml,...',
    'Accept-Language': 'en-us,en;q=0.5',
    'Sec-Fetch-Mode': 'navigate',
}
```

### ✅ 2. 指数退避重试

重试间隔：30秒 → 60秒 → 120秒
```python
wait_time = retry_delay * (2 ** attempt)  # 30s, 60s, 120s
```

## 推荐解决方案

### 🔥 方案1：安装Node.js（最有效）

YouTube现在要求JavaScript运行时来解密某些视频URL。

#### Windows安装：

1. **下载Node.js**  
   访问：https://nodejs.org/  
   下载LTS版本（推荐20.x）

2. **安装**  
   双击安装包，默认选项即可

3. **验证安装**
   ```bash
   node --version
   # 应该显示：v20.x.x
   ```

4. **重启终端并测试**
   ```bash
   python main.py
   ```

**效果**：yt-dlp会自动检测并使用Node.js，403错误应该消失

---

### 🍪 方案2：使用Cookie认证（备选）

登录YouTube账号可以获得更好的访问权限。

#### 步骤：

1. **导出Cookie**
   
   使用Chrome扩展：  
   - [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   
   或Firefox扩展：
   - [cookies.txt](https://addons.mozilla.org/firefox/addon/cookies-txt/)

2. **导出YouTube的Cookies**
   
   - 打开youtube.com
   - 登录账号
   - 点击扩展图标
   - 选择"Export Cookies for this site"
   - 保存为 `youtube_cookies.txt`

3. **放置Cookie文件**
   ```
   Subtitle/
     youtube_cookies.txt  ← 放这里
   ```

4. **修改配置**
   
   编辑 `config.yaml`:
   ```yaml
   downloader:
     cookies_file: "./youtube_cookies.txt"  # 取消注释并修改路径
   ```

5. **测试**
   ```bash
   python main.py
   ```

**注意**：Cookie会过期，需定期更新（通常1-3个月）

---

### ⏱️ 方案3：增加延迟（临时）

如果上述方案暂时无法实施，可以增加重试延迟。

编辑 `config.yaml`:
```yaml
downloader:
  download_delay: 600  # 从300改为600秒（10分钟）
  max_retries: 5       # 增加重试次数
```

**缺点**：下载会非常慢，但可能避免403错误

---

### 🔽 方案4：降低视频质量（最后手段）

某些低质量格式不需要特殊验证。

编辑 `config.yaml`:
```yaml
downloader:
  video_quality: "720p"  # 或 "480p"
```

**优点**：更稳定，不易触发403  
**缺点**：视频质量降低

---

## 测试当前修复

已经应用了浏览器伪装和指数退避，可以先测试：

```bash
# 清除缓存
Remove-Item -Recurse -Force cache\u6Ype20iN2k -ErrorAction SilentlyContinue

# 重试
python main.py
```

**观察日志**：
- ✅ 如果成功：说明浏览器伪装有效
- ❌ 如果仍403：建议安装Node.js（方案1）

---

## 长期建议

### 最佳实践组合：

1. ✅ **安装Node.js** - 解决JS runtime警告
2. ✅ **使用Cookie** - 降低被检测概率
3. ✅ **保持更新** - `pip install --upgrade yt-dlp`
4. ✅ **合理延迟** - 避免频繁下载

### 命令行测试：

```bash
# 测试yt-dlp命令行是否正常
yt-dlp -f "bestvideo[height<=1080]+bestaudio" https://www.youtube.com/watch?v=u6Ype20iN2k

# 如果命令行正常但代码失败，说明是代码配置问题
# 如果命令行也失败，说明是YouTube限制或网络问题
```

---

## 警告信息说明

### JavaScript Runtime警告：
```
WARNING: No supported JavaScript runtime could be found
```
→ **解决**：安装Node.js

### Visitor Data警告：
```
WARNING: Missing required Visitor Data
```
→ **影响**：某些客户端的高质量格式被跳过（已通过默认配置绕过）

### SABR Streaming警告：
```
WARNING: Some web client https formats have been skipped (SABR streaming)
```
→ **影响**：Web客户端部分格式不可用（已通过默认配置绕过）

---

## 当前状态总结

| 项目 | 状态 | 说明 |
|------|------|------|
| 格式选择 | ✅ 正确 | 399+251 (1080p, 572MB) |
| 浏览器伪装 | ✅ 已添加 | User-Agent等HTTP头 |
| 重试策略 | ✅ 已优化 | 指数退避 30s→60s→120s |
| JS Runtime | ⚠️ 缺失 | 需安装Node.js |
| Cookie认证 | ❌ 未配置 | 可选，提升稳定性 |

**下一步**：测试当前修复 → 如果仍失败，安装Node.js
