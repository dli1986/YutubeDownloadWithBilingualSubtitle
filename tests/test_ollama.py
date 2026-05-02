"""
测试Ollama连接
"""

import ollama

try:
    client = ollama.Client(host='http://localhost:11434')
    models = client.list()
    
    print("✓ Ollama连接成功！")
    print("\n可用模型：")
    for model in models.get('models', []):
        name = model.get('name', 'unknown')
        size = model.get('size', 0) / (1024**3)  # 转换为GB
        print(f"  - {name} ({size:.1f} GB)")
    
    # 测试生成
    print("\n测试文本生成...")
    response = client.generate(
        model='qwen3:8b',
        prompt='翻译为中文：Hello, how are you?',
        options={'temperature': 0.3}
    )
    print(f"✓ 生成成功: {response['response']}")
    
except Exception as e:
    print(f"✗ Ollama连接失败: {e}")
    print("\n请检查：")
    print("  1. Ollama服务是否运行：ollama serve")
    print("  2. 端口是否正确：http://localhost:11434")
    print("  3. 模型是否已下载：ollama list")
