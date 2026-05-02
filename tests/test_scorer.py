import sys
sys.path.insert(0, r'C:\Users\dli\Projects\MyTest\Subtitle')
from transcriber import SemanticBreakScorer

sc = SemanticBreakScorer()

tests = [
    ("We're building duckling",         "for developers for data scientists",     0.0,  "Layer 2.7 predicate open arg"),
    ("in the open-source",              "document AI space.",                      0.0,  "Layer 2.6 phrase root anchored"),
    ("the Python SDK",                  "which comes with it.",                    0.0,  "Layer 2.5 relcl open complement"),
    ("for data engineers.",             "The real power actually lies",            1.0,  "Layer 0 sentence boundary"),
    ("document AI",                     "space.",                                  0.05, "Layer 1 direct compound (left modifies right)"),
    ("for developers.",                 "Now let me show",                         1.0,  "Layer 0 sentence boundary"),
]

print(f"{'Score':>6}  {'Expected':>8}  {'Pass':>4}  Description")
print("-" * 70)
all_pass = True
for left, right, expected, desc in tests:
    s = sc.score(left, right)
    ok = (s == expected) or (expected == 1.0 and s >= 0.9)
    all_pass = all_pass and ok
    mark = "OK" if ok else "FAIL"
    print(f"{s:>6.2f}  {expected:>8.2f}  {mark:>4}  {desc}")
    if not ok:
        print(f"         left={left!r} | right={right!r}")

print()
print("ALL PASS" if all_pass else "SOME FAILURES")
