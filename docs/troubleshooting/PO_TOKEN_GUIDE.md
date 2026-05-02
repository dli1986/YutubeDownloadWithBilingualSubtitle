# YouTube PO Token 和格式访问问题

## 问题说明

### 当前错误
```
WARNING: [youtube] android client https formats require a GVS PO Token
WARNING: [youtube] Some web client formats have been skipped (SABR streaming)
[info] Downloading 1 format(s): 18  ← 只能下载360p低质量
```

### 根本原因

YouTube在2024年底开始要求某些客户端使用**PO Token**才能访问高质量格式：

| 客户端 | 高质量访问 | 需要PO Token | SABR限制 |
|--------|-----------|--------------|---------|
| **Android** | ✓ | ⚠️ **需要** | 无 |
| **iOS** | ✓ | ❌ **不需要** | 无 |
| **Web** | 部分 | 无 | ⚠️ **有** |
| **TV** | ✓ | 需要 | 无 |

**结果**：
- Android客户端：需要PO Token → 403错误 → 跳过高质量格式
- Web客户端：SABR限制 → 跳过高质量格式
- 最终只能下载格式18（360p，109MB）

## 解决方案

### ✅ 方案1：使用iOS客户端（推荐）

```python
# downloader.py
'extractor_args': {
    'youtube': {
        'player_client': ['ios', 'web'],  # iOS优先
    }
}
```

**优点**：
- ✓ 不需要PO Token
- ✓ 可访问高质量格式
- ✓ 配置简单

**缺点**：
- 某些视频可能不支持

### ⚠️ 方案2：获取PO Token

如果iOS客户端不行，需要获取PO Token。

#### 步骤：

1. **安装yt-dlp扩展**
   ```bash
   pip install --upgrade yt-dlp
   ```

2. **获取PO Token**
   
   访问：https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
   
   有两种方法：
   
   **方法A：使用浏览器扩展（简单）**
   - 安装Chrome/Firefox扩展
   - 登录YouTube
   - 自动获取Token
   
   **方法B：手动提取（复杂）**
   - 需要抓取Android应用流量
   - 提取Token和设备ID

3. **配置Token**
   
   ```bash
   # 方式1：命令行
   yt-dlp --extractor-args "youtube:po_token=android.gvs+XXX" URL
   
   # 方式2：配置文件（推荐）
   ```
   
   创建 `~/.config/yt-dlp/config` 或 `yt-dlp.conf`:
   ```
   --extractor-args "youtube:po_token=android.gvs+YOUR_TOKEN_HERE"
   ```

4. **在代码中使用**
   
   ```python
   # config.yaml
   downloader:
     po_token: "android.gvs+YOUR_TOKEN_HERE"  # 添加这行
   
   # downloader.py
   if self.config.get('po_token'):
       ydl_opts['extractor_args']['youtube']['po_token'] = self.config['po_token']
   ```

### 📋 方案3：使用Cookie（备选）

登录YouTube账号可能获得更好的访问权限。

1. **导出Cookie**
   
   使用浏览器扩展：
   - Chrome: "Get cookies.txt"
   - Firefox: "cookies.txt"
   
   导出 `youtube.com` 的cookies

2. **配置Cookie文件**
   
   ```yaml
   # config.yaml
   downloader:
     cookies_file: "./cookies.txt"
   ```

## 测试脚本

运行测试查看可用格式：

```bash
python test_formats.py
```

输出示例：
```
客户端: iOS
==================================================
  ✓ 找到 15 个视频格式

  前5个最高质量格式:
  ID       分辨率        编码        文件大小         FPS
  ------------------------------------------------------------
  137      1920x1080    vp9        2.1 GB          30
  136      1280x720     vp9        1.1 GB          30
  135      854x480      vp9        500 MB          30
  ...

  推荐1080p格式: 137
```

## 当前修复

已修改为使用iOS客户端：

```python
'player_client': ['ios', 'web']  # iOS优先，Web备用
```

**测试命令**：
```bash
# 清除缓存重新测试
rm -rf cache/99ko-QPJ4uQ
python main.py
```

**预期结果**：
```
[info] 99ko-QPJ4uQ: Downloading format(s): 137+140  ← 1080p+音频
[download] 100% of 2.15GiB
```

## 如果iOS方案不行

### 备选A：降低质量要求

```yaml
# config.yaml
video_quality: "720p"  # 或 "480p"
```

某些低质量格式不需要特殊权限。

### 备选B：使用yt-dlp命令行

```bash
# 直接用yt-dlp测试
yt-dlp -f "bestvideo[height<=1080]+bestaudio" URL

# 如果成功，说明是代码配置问题
# 如果失败，说明是YouTube限制
```

### 备选C：等待或更新yt-dlp

YouTube的限制政策经常变化，yt-dlp会持续更新：

```bash
pip install --upgrade yt-dlp
```

## 长期方案

为了避免未来的限制：

1. **保持yt-dlp更新**
   ```bash
   pip install --upgrade yt-dlp
   ```

2. **使用Cookie认证**
   - 登录YouTube Premium可能有更好权限
   - 定期更新Cookie文件

3. **监控客户端变化**
   - iOS客户端目前最稳定
   - 如果失效，切换到其他客户端

4. **考虑配置PO Token**
   - 一劳永逸的解决方案
   - 需要定期更新Token

## 参考资料

- [yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- [yt-dlp SABR Issue](https://github.com/yt-dlp/yt-dlp/issues/12482)
- [yt-dlp GitHub](https://github.com/yt-dlp/yt-dlp)

## 总结

**当前修复**：
- ✅ 改用iOS客户端
- ✅ 提供测试脚本
- ✅ 文档说明备选方案

**下一步**：
1. 运行 `python test_formats.py` 查看可用格式
2. 运行 `python main.py` 测试下载
3. 如果仍失败，考虑配置PO Token或Cookie

希望iOS客户端能解决问题！🤞
