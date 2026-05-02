import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.transcriber import SemanticBreakScorer

sc = SemanticBreakScorer()

tests = [
    # Rule: Locative advmod (Layer 2.7b) — MUST BLOCK
    ("find what you need",  "there.",                              0.0,  "2.7b locative advmod 'there'"),
    ("go to the GitHub website and you will be finding what you need", "there.", 0.0, "2.7b practical example"),
    # Discourse shift CCONJ (Layer 3) — MUST ALLOW
    ("interesting I think in corporate contexts",  "And you can find tons of examples", 0.60, "L3 CCONJ discourse shift"),
    # Previous 2.7 — still blocks
    ("We're building duckling",  "for developers for data scientists", 0.0, "2.7 open prep arg"),
    # Layer 2 still allows new clause
    ("for developers.",     "The real power actually lies",  1.0,   "L0 sentence boundary"),
    # direct compound still blocks  
    ("document AI",         "space.",                        0.05,  "L1 left modifies right"),
    # Layer 2.5 relcl still blocks
    ("the Python SDK",      "which comes with it.",          0.0,   "2.5B relcl complement"),
]

print(f"{'Score':>6}  {'Exp':>6}  {'Pass':>4}  Description")
print("-" * 70)
all_pass = True
for left, right, exp, desc in tests:
    s = sc.score(left, right)
    if exp >= 0.9:
        ok = s >= 0.9
    elif exp == 0.60:
        ok = s >= 0.55  # discourse shift — accept anything > SAFE_BREAK_THRESHOLD
    elif exp == 0.0:
        ok = s == 0.0
    else:
        ok = abs(s - exp) < 0.1
    all_pass = all_pass and ok
    mark = "OK" if ok else "FAIL"
    print(f"{s:>6.2f}  {exp:>6.2f}  {mark:>4}  {desc}")
    if not ok:
        print(f"         left={left!r}")
        print(f"         right={right!r}")

print()
print("ALL PASS" if all_pass else "SOME FAILURES")
