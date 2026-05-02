# 注意：必须从项目根目录运行：python tests/analyze_srt.py
import re

with open('cache/xUxhqJLVgRE/subtitle.en.srt', encoding='utf-8') as f:
    content = f.read()

blocks = re.split(r'\n\n', content.strip())
issues = []
func_words = {
    'the','a','an','in','of','to','for','on','at','by','with','from',
    'and','or','but','so','yet','as','that','which','who','when','where',
    'if','than','about','over','into','what','both','not','how'
}

for b in blocks:
    lines = b.strip().split('\n')
    if len(lines) < 3:
        continue
    num = lines[0]
    text = ' '.join(lines[2:]).strip()
    words = text.replace('\n', ' ').split()
    if not words:
        continue
    last_w = words[-1].lower().rstrip('.,!?;:"\'-')
    if len(words) == 1:
        issues.append((int(num), 'SINGLE_WORD', text))
    elif last_w in func_words:
        issues.append((int(num), f'ENDS_FUNC({last_w})', text[:90]))

print(f'Total SRT entries: {len(blocks)}')
print(f'Total issues: {len(issues)}')
print()
for n, t, tx in sorted(issues)[:40]:
    print(f'  #{n:4d} [{t}]: {tx}')
