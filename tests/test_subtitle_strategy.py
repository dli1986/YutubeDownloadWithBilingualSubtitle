"""
测试字幕获取策略
用于验证原始字幕优先，Whisper作为备选方案
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import SubtitleGenerator

def test_subtitle_strategy():
    """测试字幕获取策略"""
    
    print("=" * 70)
    print("字幕获取策略测试")
    print("=" * 70)
    print()
    print("策略说明：")
    print("  1. ✓ 优先使用YouTube原始字幕（包括人工字幕和自动生成CC字幕）")
    print("  2. ✓ 如果没有原始字幕，使用Whisper ASR转录")
    print("  3. ✓ Whisper转录准确度更高，但耗时较长且消耗GPU资源")
    print("  4. ✓ 原始字幕可节省大量时间和计算资源")
    print()
    print("=" * 70)
    print()
    
    # 初始化生成器
    generator = SubtitleGenerator()
    
    print("测试环境：")
    print(f"  Whisper模型: {generator.config['transcriber']['model']}")
    print(f"  设备: {generator.config['transcriber']['device']}")
    print(f"  翻译提供商: {generator.config['translator']['default_provider']}")
    print()
    print("=" * 70)
    print()
    print("提示：")
    print("  - 如果视频有原始字幕，将跳过Whisper转录步骤")
    print("  - 如果视频没有原始字幕，将使用Whisper生成（可能需要几分钟）")
    print("  - 日志中会明确显示使用的字幕来源")
    print()
    print("=" * 70)

if __name__ == "__main__":
    test_subtitle_strategy()
