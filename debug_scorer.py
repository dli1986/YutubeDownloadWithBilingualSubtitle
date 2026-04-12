import sys
sys.path.insert(0, r'C:\Users\dli\Projects\MyTest\Subtitle')
from transcriber import SemanticBreakScorer

sc = SemanticBreakScorer()

def show_parse(left, right):
    combined = (left + ' ' + right).strip()
    doc = sc._parse(combined)
    split_idx = len(left.split()) - 1
    print(f"\n--- left={left!r} | right={right!r}")
    print(f"    split_idx={split_idx}  (right_tok = doc[{split_idx+1}])")
    for tok in doc:
        marker = '<LEFT_END' if tok.i == split_idx else ('<RIGHT_START' if tok.i == split_idx+1 else '')
        print(f"  [{tok.i:2d}] {tok.text:20s}  pos={tok.pos_:8s}  dep={tok.dep_:12s}  head=[{tok.head.i}]{tok.head.text}  {marker}")

show_parse("in the open-source",     "document AI space.")
show_parse("the Python SDK",          "which comes with it.")
show_parse("document AI",             "space.")
