# Benign-extra Sensitivity Scoring Policy V1 — FROZEN

> **冻结时点**：在读取/裁决三模型具体 sensitivity candidate raw responses 之前。  
> **作用**：定义 strict Primary scoring 之外的、仅用于 robustness/sensitivity analysis 的保守宽容评分。  
> **Primary scoring 永久不修改。**

## 1. 分析范围

Sensitivity 只重评分同一批 540 个 **Initial** observation：

```text
90 tasks × 3 models × 2 conditions = 540
```

禁止：

- 新增 task；
- 再调用任何模型；
- 修改 Formal90 / C1 / validator / repair protocol；
- 重新定义 Primary Task Success；
- 因看到 sensitivity 成绩而修改本 policy。

本轮 sensitivity **不重新定义 repair / final-after-one-repair**。

原因：repair eligibility 是由 strict Initial failure 决定的。如果 sensitivity 把某些 Initial FAIL 改成 PASS，就会产生“这个 case 在 sensitivity 世界里本来不应该 repair”的反事实选择问题；现有 repair response 又是在 strict protocol 下生成，因此不能把两套 denominator 混起来。

---

## 2. Candidate gate

只有同时满足以下条件的 strict Initial FAIL 才进入 benign-extra adjudication：

1. `initial_success = false`；
2. parser authoritative status = `OK`；
3. failure layer = `STATIC`；
4. error codes 只属于 extra-family：
   - `UNAUTHORIZED_SETTING`
   - `UNSUPPORTED_VALIDATOR_KEY`
   - `UNAUTHORIZED_EXTRA_SUBMIT_OPTION`
5. 不存在其他 substantive error。

只要存在以下任何错误，直接保持 sensitivity FAIL，不进入“benign extra 救回”：

- `PARSER_NOT_OK`
- `UNKNOWN_SUBMIT_OPTION`
- `MODEL_POSITIONAL_ARGUMENT_NOT_ALLOWED`
- `MISSING_REQUIRED_SETTING`
- `TASK_VALUE_MISMATCH`
- `INVALID_VALUE`
- `DISALLOWED_CONFIGURATION_SOURCE`
- `MULTIPLE_EFFECTIVE_VALUES`

---

## 3. 单个 extra 的三分类

每个额外 setting / submit option 必须单独判定：

```text
BENIGN
HARMFUL
AMBIGUOUS
```

最终默认原则：

```text
AMBIGUOUS → FAIL
```

即 sensitivity 是**保守宽容**，不是“尽量救回”。

---

## 4. BENIGN 必须同时满足全部条件

一个 extra 只有在全部成立时才可判 `BENIGN`：

1. 它是 Spark 3.5.9 / YARN 下被识别的配置 setting 或 spark-submit option；未知语法永远不算 benign。
2. value 在冻结 Spark 3.5.9 语义下合法。
3. task 要求的全部 key、value、source、precedence 已经独立正确，不依赖该 extra 才成立。
4. 该 extra 不会改变任何 required setting 的最终 effective value 或 effective source。
5. 它对该 task **behaviorally inert**：不能改变 task rule、runtime assertion、资源行为、部署行为、依赖/交互关系或其必要前提。
6. 必须能用冻结的 task specification、Spark 3.5.9 官方文档或冻结实验 fixture 证明其“无行为影响”；模型身份和模型总体成绩绝不能作为证据。
7. 不能高置信证明无害时，一律 `AMBIGUOUS`。

---

## 5. 允许出现的 benign mechanism class

### B_ENVIRONMENT_RESTATEMENT

模型显式重复了实验环境已经独立强制的 invariant，并且：

- task 不测试这个 invariant；
- task 也不测试依赖它的行为；
- extra 不会改变实际 effective behavior。

### B_METADATA_ONLY

被 Spark 识别、但只影响 metadata，不可能影响当前 task 的配置语义或 runtime assertions。

### B_DUPLICATE_EQUIVALENT

完全相同语义和值的重复表达，但仅在以下情况允许：

- 不改变 source / precedence；
- 不制造 multiple effective values；
- task 不测试 configuration source / precedence。

---

## 6. 必须保持非 benign 的情况

### H_CONFLICT_REQUIRED

与 task required value、instruction 或 fixture 冲突。

### H_CHANGES_BEHAVIOR

会改变当前 task 的执行语义。

### H_SOURCE_PRECEDENCE

改变 source / precedence，或产生多个 competing effective values。

### H_FEATURE_TOGGLE

开启/关闭某功能，且该状态可能影响执行或 task semantics。

### H_RESOURCE_SEMANTICS

改变 executor/driver memory、overhead、cores、instances、dynamic scaling 等资源语义，除非能证明只是外部已强制且与该 task 完全无关的 invariant restatement。

### H_DEPLOYMENT_SEMANTICS

改变 master、deploy mode、driver placement 等部署语义，除非能证明只是外部已强制且与该 task 完全无关的 invariant restatement。

### H_UNKNOWN_OR_INVALID

未知 setting/option、非法 value、无法由冻结语义验证的语法。

### A_INSUFFICIENT_EVIDENCE

语义上“看起来可能没关系”，但冻结证据不足以证明 inertness。

此类统一：

```text
AMBIGUOUS → FAIL
```

---

## 7. 禁止使用的捷径

以下 setting 即使在已有摘要中曾被观察到，也**不能预先自动放行**：

```text
--master yarn
--deploy-mode client/cluster
spark.dynamicAllocation.enabled=false
```

它们必须逐 task 按冻结规则判断。

同样：

```text
UNSUPPORTED_VALIDATOR_KEY
```

不是 benign 的同义词。

如果 validator 不支持某 key，但冻结官方语义能够证明它合法且 behaviorally inert，才有可能判 benign；证据不足则 `AMBIGUOUS`。

---

## 8. Case-level sensitivity scoring

对 observation \(j\)：

\[
S_{strict,j}\in\{0,1\}
\]

Sensitivity：

\[
S_{sens,j}=1
\]

当且仅当：

1. strict 已经 PASS；或
2. strict FAIL 且通过 candidate gate，并且**所有** unauthorized extras 均为 `BENIGN`，且不存在任何其他 substantive error。

因此：

```text
strict PASS → sensitivity PASS
strict FAIL → 只能 0→1
```

不会出现：

```text
strict PASS → sensitivity FAIL
```

---

## 9. Blinded adjudication

在裁决 extra 时，不显示：

- model；
- C0/C1 condition；
- 各模型总体成功率；
- 某 extra 在哪个模型中出现频率。

允许显示：

- blind_case_id；
- task_id；
- task prompt；
- category；
- rule_ids；
- required_exact_keys；
- oracle/task specification；
- raw generated configuration；
- authoritative parser result；
- authoritative static validation evidence。

model / condition 的 private mapping 留在用户本地，不打进回传 ZIP。

---

## 10. 判定冻结以后才 unblind

顺序固定：

```text
policy freeze
→ policy hash
→ extract candidates
→ blind adjudication
→ adjudication decision file freeze + hash
→ local unblind
→ sensitivity rescoring
→ statistics
```

这样可避免根据模型身份或结果方向修改判定。

---

## 11. Sensitivity 后重新运行哪些统计

只针对 **Initial sensitivity success**，完全复用 Primary Statistics V1：

1. success counts + Wilson 95% CI；
2. C0/C1 paired transition；
3. exact McNemar；
4. 三模型 headline McNemar 的 Holm correction；
5. 同样的 A–F stratified task-level bootstrap：
   - 10,000 replicates；
   - seed = `20261001`；
6. 同样的 GLMM：

```r
sensitivity_success ~ model * condition + category + (1 | task_id)
```

7. 同样的 diagnostics；
8. 若 GLMM 不满足门槛，同样使用预先规定的 GEE fallback。

另外描述：

- strict→sensitivity flips；
- flips by model/condition；
- flips by A–F；
- flips by benign mechanism class。

---

## 12. 最终论文解释

Sensitivity 不取代 Primary。

论文始终按：

```text
Primary = frozen strict scoring
Sensitivity = benign-extra-tolerant robustness analysis
```

如果两套结论一致：

> 结论对 scoring strictness 稳健。

如果 GPT 等模型在 sensitivity 下明显提高：

> 说明部分 strict failure 来源于 over-specification，而不是 required setting 本身错误；minimal task-conforming correctness 与 semantically acceptable configuration 是不同的 reliability concepts。

无论哪种结果，都不能反过来修改 Primary。
