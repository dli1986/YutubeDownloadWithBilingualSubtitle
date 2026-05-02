"""
测试YouTube视频格式可用性
用于诊断下载问题
"""

import yt_dlp

url = "https://www.youtube.com/watch?v=99ko-QPJ4uQ"

print("=" * 70)
print("测试不同客户端的格式可用性")
print("=" * 70)

# 测试不同的客户端配置
clients = [
    ('iOS', ['ios']),
    ('iOS + Web', ['ios', 'web']),
    ('Android', ['android']),
    ('Android + Web', ['android', 'web']),
    ('Web', ['web']),
    ('默认', None),
]

for name, client_list in clients:
    print(f"\n{'='*70}")
    print(f"客户端: {name}")
    print('='*70)
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
    }
    
    if client_list:
        ydl_opts['extractor_args'] = {
            'youtube': {
                'player_client': client_list
            }
        }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = info.get('formats', [])
            
            # 筛选视频格式（有高度信息的）
            video_formats = [f for f in formats if f.get('height')]
            
            if not video_formats:
                print("  ❌ 无可用视频格式")
                continue
            
            # 按分辨率排序
            video_formats.sort(key=lambda x: x.get('height', 0), reverse=True)
            
            # 显示前5个最高质量的
            print(f"\n  ✓ 找到 {len(video_formats)} 个视频格式")
            print(f"\n  前5个最高质量格式:")
            print(f"  {'ID':<8} {'分辨率':<12} {'编码':<10} {'文件大小':<15} {'FPS':<5}")
            print(f"  {'-'*60}")
            
            for fmt in video_formats[:5]:
                fmt_id = fmt.get('format_id', 'N/A')
                height = fmt.get('height', 0)
                width = fmt.get('width', 0)
                vcodec = fmt.get('vcodec', 'N/A')[:8]
                filesize = fmt.get('filesize', 0)
                fps = fmt.get('fps', 'N/A')
                
                if filesize:
                    size_mb = filesize / (1024 * 1024)
                    size_str = f"{size_mb:.1f} MB"
                else:
                    size_str = "未知"
                
                resolution = f"{width}x{height}"
                
                print(f"  {fmt_id:<8} {resolution:<12} {vcodec:<10} {size_str:<15} {fps}")
            
            # 显示推荐格式
            best_1080p = next((f for f in video_formats if f.get('height') <= 1080), None)
            if best_1080p:
                print(f"\n  推荐1080p格式: {best_1080p.get('format_id')}")
    
    except Exception as e:
        print(f"  ❌ 提取失败: {e}")

print("\n" + "=" * 70)
print("建议：")
print("  1. 如果iOS客户端有高质量格式，使用 player_client: ['ios']")
print("  2. 如果需要PO Token，参考: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide")
print("  3. 考虑使用Cookie文件登录YouTube账号")
print("=" * 70)
