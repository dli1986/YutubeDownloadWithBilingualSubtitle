import pysrt
en = pysrt.open(r'C:\Users\dli\Projects\MyTest\Subtitle\cache\xUxhqJLVgRE\subtitle.en.srt', encoding='utf-8')

BAD_ENDINGS = {
    'the','a','an','in','of','to','for','on','at','by','with','from','and','or','but',
    'so','yet','as','that','which','who','if','than','about','what','how','not',
    'it','he','she','they','we','you','this','its','their','our',
    'been','be','have','has','is','are','was','were','will','would','could','should',
    'can','may','might','do','does','did',
    'very','just','also','only','quite','really','actually','still','even','already',
}
issues = []
for idx, s in enumerate(en):
    words = s.text.replace('\n', ' ').split()
    if not words:
        continue
    last = words[-1].lower().rstrip('.,!?;:"\'-')
    if last in BAD_ENDINGS:
        issues.append((idx + 1, last, s.text.replace('\n', ' ')[:75]))

print(f'Total issues: {len(issues)} / {len(en)}')
for n, l, t in issues[:35]:
    print(f'  #{n:4d} [ends:{l:10s}]: {t}')
