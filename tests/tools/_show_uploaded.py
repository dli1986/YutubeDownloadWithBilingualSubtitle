import json

CACHE_FILE = './cache/processed_videos.json'
RESET_KEYS = ['upload_status', 'bvid', 'uploaded_at', 'series_fixed', 'upload_error']

with open(CACHE_FILE, encoding='utf-8') as f:
    cache = json.load(f)

count = 0
for vid, v in cache.items():
    meta = v.get('metadata', {})
    if meta.get('upload_status') == 'uploaded' and meta.get('bvid', '') not in ('', 'manual'):
        for k in RESET_KEYS:
            meta.pop(k, None)
        count += 1
        print(f'重置: {vid}')

with open(CACHE_FILE, 'w', encoding='utf-8') as f:
    json.dump(cache, f, ensure_ascii=False, indent=2)

print(f'完成：共重置 {count} 条记录')
