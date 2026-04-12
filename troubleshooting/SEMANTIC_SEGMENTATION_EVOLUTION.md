# 语义分段系统演化记录

> 记录从最初 LLM hard segmentation 到最终 deterministic-first + LLM soft advisory 架构的完整演化过程，以及沿途引入的每一层语义约束手段。

---

## 一、问题起点

### 原始 LLM Hard Segmentation 架构

```
json3_extract_words()
    ↓
segment_captions() ← LLM 决定哪些词组成一个字幕块
    ↓ groups = [[0,1,2,3], [4,5,6], ...]  (hard boundaries)
words_to_srt()     ← 应用展示规则
```

**根本缺陷（system-level bug）：**

| 问题 | 原因 |
|---|---|
| 同一段词流每次分割结果不同 | LLM `temperature > 0` 导致采样随机 |
| SemanticBreakScorer 效果不稳定 | 输入 groups 由 LLM 决定，上游污染下游 |
| 硬切点不可预期 | LLM 被要求输出整数偏移，等价于 hard segmentation |

**字幕铁律**：hard segmentation 必须是 deterministic —— LLM 不擅长这个任务，因为它本质上是统计生成模型，而非规则推理器。

---

## 二、理论框架转变

用户研究确立的核心原则：

> **Semantic integrity > line length > frame count**
> **Symbolic hard constraints → LLM soft suggestion**

正确的角色分配：

| 层级 | 职责 | 确定性 |
|---|---|---|
| 停顿检测 + 句末标点 | 必须断的地方 | ✅ deterministic |
| SemanticBreakScorer | 每个候选边界的语义安全分 | ✅ deterministic |
| LLM advisor | 模糊边界的 Y/N 软建议 | ❌（可接受，权重上限 0.25） |
| 最终裁决 | 规则引擎：字符数 + CPS + 分数综合 | ✅ deterministic |

---

## 三、最终架构（enabled=true 路径）

```
[1] json3_extract_words()
    → 从 YouTube json3 提取词级精确时间戳
    → 完全确定性

[2] compute_boundary_scores(words)
    → 为每个词边界打分 0.0 – 1.0
    → 三层规则（见第四节）
    → 完全确定性

[3] validate_breaks_llm(words, ambiguous_candidates)
    → 仅对 score ∈ [0.30, 0.65] 的模糊边界发起询问
    → 问："这里开始新字幕是否语义完整？Y/N"
    → 返回 {word_idx: bool}，权重上限 ±0.25
    → 硬 block (score=0.0) 绝不发送给 LLM

[4] build_groups_from_scores(words, scores, llm_votes)
    → LLM 投票后，规则引擎做最终裁决
    → score=0.0  → 永不切
    → score≥0.75 → 必切
    → 超字符/时长限制 → 强制切
    → 返回 groups = [[word_idx,...], ...]

[5] words_to_srt(words, groups, ...)
    → _cc_safe_groups()  CC 语义原子性后处理
    → sub-split 循环  超长组再切分
    → CPS / 时长 / 行宽约束
```

---

## 四、compute_boundary_scores — 三层确定性评分

### Layer 0: 句末标点（立即返回）

```python
if last_char in '.!?':   return 1.0
```

### Layer 2: 语义硬 block（立即返回）

```python
if SemanticBreakScorer.score(left, right) <= HARD_BLOCK(0.05):
    return 0.0
```

### Layer 3–4: 综合信号

```python
combined = max(punct_s,  0.55 * sem_s  +  0.45 * pause_s)
```

- `punct_s`  = 0.7 if `,;:` else 0.0
- `pause_s`  = min(1.0, gap_ms / 1500)
- `sem_s`    = SemanticBreakScorer.score(left_ctx, right_ctx)

---

## 五、SemanticBreakScorer — 渐进式迭代

### V1：纯启发式（无 spaCy）

```
Layer 0: 句末标点 → 1.0
Layer 1: 左端以功能词结尾 → 0.0
Layer fallback: 0.3 / 0.6
```

**问题**：无法处理 `"document AI | space."` 这类依存关系依赖的断点。

---

### V2：引入 spaCy 依存句法（en_core_web_sm）

**新增层：**

| 层 | 触发条件 | 分数 |
|---|---|---|
| Layer 1（重命名）| `right.dep_ in BLOCKING_DEPS` 且 `right.head == left` | 0.0 |
| Layer 1b | `left.dep_ in BLOCKING_DEPS` 且 `left.head == right` | 0.05 |
| Layer 2 | `right.dep_ in GOOD_RIGHT_DEPS`（nsubj/ROOT）| 0.9 |
| Layer 3 | `right.pos_ in BAD_RIGHT_POS`（DET/ADP/CCONJ/PART）| 0.1 |
| Layer 4 | Open arc count 惩罚 | 0.20–0.70 |

**BLOCKING_DEPS**：`compound, amod, det, poss, nummod, nmod, npadvmod, quantmod, predet, nn`

**验证的 5 个 test case（全部通过）：**

```
'document AI'       | 'space.'      → 0.05  ✓
'...and it'         | 'uh quickly'  → 0.00  ✓
'...for developers.'| 'Now let me'  → 1.00  ✓
'in the open-source'| 'document AI' → 0.20  ✓
'...the enrichment' | 'process.'    → 0.05  ✓
```

---

### V3：Predicate Completion Constraint（谓语完整性约束）

**问题案例**：`"We're building duckling"` 被单独成块，但 `"for developers..."` 是其开放补语。

**关键 Bug Fix —— split_idx 字符偏移修正：**

原来用 `len(left.split()) - 1` 计数词，但 spaCy 会把 `"open-source"` 分成 3 个 token (`open`, `-`, `source`)，导致边界偏移错误。改为按字符位置找最后在 `left_text` 范围内的 token。

**新增三层：**

```python
# Layer 2.5A: right 是开放补语/关系从句，head 在 left span 内 → 0.0
OPEN_COMP_DEPS = {'prep','mark','relcl','acl','advcl','xcomp','ccomp','pcomp'}
if right_tok.dep_ in OPEN_COMP_DEPS and right_tok.head.i <= split_idx:
    return 0.0

# Layer 2.5B: right 开启一个修饰 left-span 名词的关系从句 → 0.0
# e.g. "the Python SDK | which comes with it"
if right_tok.head.dep_ in ('relcl','acl') and right_tok.head.head.i <= split_idx:
    return 0.0

# Layer 2.6: right 是 compound 链，其短语根由 left-span 支配 → 0.0
# e.g. "in the open-source | document AI space."
if right_tok.dep_ in BLOCKING_DEPS:
    head = right_tok
    for _ in range(5):
        if head.dep_ not in BLOCKING_DEPS: break
        head = head.head
    if head.i > split_idx and head.head.i <= split_idx:
        return 0.0

# Layer 2.7: left-span 内的 VERB 有开放论元（prep/dobj/etc.）在 right → 0.0
VERB_ARG_DEPS = {'prep','dobj','xcomp','ccomp','attr','acomp','agent','oprd'}
for tok in doc[:split_idx+1]:
    if tok.pos_ in ('VERB','AUX') and tok.dep_ in ('ROOT','ccomp','advcl','xcomp','relcl','acl'):
        for child in tok.children:
            if child.i > split_idx and child.dep_ in VERB_ARG_DEPS:
                return 0.0
```

---

### V4：命题完整性约束（Clause/Proposition Completion）

**解决的问题**：

- Entry #98: `"there."` 单词孤块（locative advmod 必须跟随动词）
- Entry #93: 超长单句（`"And you can find..."` 被 CCONJ 阻断，无法断句）

**新增机制：**

**Layer 2.7b — Locative VP Completion：**
```python
LOCATIVE_ADVS = {'there','here','away','back','out','ahead','forward','together','along','apart'}
if child.dep_ == 'advmod' and child.text.lower() in LOCATIVE_ADVS:
    return 0.0  # "find what you need | there" → hard block
```

**Layer 3 Exception — CCONJ Discourse Shift：**
```python
# "...in corporate contexts | And you can find..." → 0.60 (允许断句)
if right_tok.pos_ == 'CCONJ':
    for sibling in right_tok.head.children:
        if sibling.dep_ in ('nsubj','nsubjpass','expl') and sibling.i > split_idx:
            return 0.60  # 新分句有主语 ⇒ discourse shift，开放断点
```

**HARD_BLOCK_THRESHOLD（sub-split 循环）：**
```python
# score < 0.1 时即使超过 SPLIT_TOLERANCE 也不强制切
if sc >= SAFE_BREAK_THRESHOLD or (over_tolerance and sc > 0.1):
    cut
```

---

## 六、_cc_safe_groups — CC 语义原子性后处理

在 `words_to_srt` 内，对 `build_groups_from_scores` 输出的 groups 做二次修复，处理 LLM / 规则引擎遗漏的边缘案例。

### Rule 1: Minimum Semantic Load（功能词结尾 → 前向合并）

```python
CC_FUNC_WORDS = {'the','a','an','in','of','to','for','on',...  # 扩展版本
    'every','each','both','this','these','those',             # 限定词
    'very','quite','just','only','even','already','actually'} # 程度副词
if last in CC_FUNC_WORDS → merge into next group
```

### Rule 2: Single-word Orphan（孤词 → 后向合并）

单词 group 合并到前一个 group（conjunction 孤词优先合并到下一个）。

### Rule 3: NP Head-completion（数量词 + 名词 → 后向合并）

前 group 末词为数量词/数字，当前 group ≤ 2 词 → 合并（`"12,000 | GitHub stars"` → together）。

### Rule 4: SemanticBreakScorer Guard（语义安全检查）

group 末尾 `scorer.score() < 0.45` → 前向合并（合并后总长 ≤ `max_chars * 1.5`）。

### Rule 5: Fragment Isolation（碎片 → 无条件合并）

```python
CC_FRAG_WORDS = {'there','here','away','back','out','ahead',...}
if len(ws) <= 2 and all(w in CC_FRAG_WORDS) → merge backward
```

**右侧碎片守卫（sub-split 循环）：**
sub-split 切割前检查 `gw[idx_w:]` 是否是 ≤2 词的 locative 碎片，是则不切。

---

## 七、CC_FUNC_WORDS 扩展历程

| 版本 | 新增词类 | 修复的问题 |
|---|---|---|
| V1 | 基础功能词（介词/连词/冠词） | 避免以 `the/in/for` 结尾 |
| V2 | 限定词 `every/each/both/such/this/these/those` | `"every"` 结尾孤词 |
| V3 | 程度/焦点副词 `very/quite/just/only/even/already/actually/really/simply/truly` | `"also new integrations coming from external communities every"` |

---

## 八、关键设计常量

```python
SAFE_BREAK_THRESHOLD   = 0.45   # score ≥ 此值 → 允许断点
HARD_BLOCK_THRESHOLD   = 0.10   # score < 此值 → 即使超宽度也不强制切
SPLIT_TOLERANCE        = max_chars * 1.3  # 强制切割的最大容忍宽度
LLM_WEIGHT             = 0.25   # LLM 投票最大影响幅度
AMBIG_LO, AMBIG_HI     = 0.30, 0.65   # LLM 仅咨询此分数区间的边界
STRONG_BREAK           = 0.75   # ≥ 此值 → 无条件断点
LONG_PAUSE_MS          = 1500   # 停顿信号归一化基准
```

---

## 九、调试工具

| 文件 | 用途 |
|---|---|
| `analyze_srt.py` | 统计 SRT 中语义原子性违规条目 |
| `analyze_srt2.py` | 详细列出各类违规（func-word 结尾 / 孤词 / 超长等）|
| `test_scorer.py` (临时) | 验证 SemanticBreakScorer 各层分数是否符合预期 |
| `debug_scorer.py` (临时) | 打印 spaCy token 级别的依存树，诊断分层逻辑错误 |

---

## 十、已知局限

1. **spaCy `en_core_web_sm` 精度有限**：对口语化表达（`uh`, `um`, 中断句）依存关系解析不准，部分 Layer 2.5-2.7 可能误判。考虑升级到 `en_core_web_lg` 或 `en_core_web_trf`。

2. **LLM 咨询延迟**：模糊边界数量多时，`validate_breaks_llm()` 会批量调用 LLM，每批 20 个边界。大视频（>30 分钟）可能增加约 1-2 分钟处理时间。

3. **enabled=false 路径未受益**：`json3_to_srt()` 停顿+标点分句走完全不同的代码路径，SemanticBreakScorer 和所有 CC 规则均不参与。两个路径的输出质量差异显著。

4. **hyphenated words 边界问题**：spaCy 把 `"open-source"` 分成 3 tokens，已通过字符偏移修复 `split_idx`，但其他连字符词（`real-world`, `end-to-end`）仍需验证。
