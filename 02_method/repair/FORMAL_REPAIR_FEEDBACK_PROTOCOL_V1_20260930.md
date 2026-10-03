# Formal Repair Feedback Protocol V1

> Freeze candidate: 2026-09-30  
> Scope: Formal 90 and later experiments only  
> Pilot status: Qwen and DeepSeek Pilot results remain unchanged and are not rerun.

## 1. Why this protocol exists

Pilot repair used the frozen validator diagnostics directly. The Pilot exposed a
methodological issue: some validator objects include fields such as `expected`,
`expected_normalized`, `expected_type`, or `allowed_sources`. These fields can
make repair easier by directly revealing hidden oracle information.

Formal Repair Feedback Protocol V1 therefore separates:

- **validation/scoring evidence**, which may contain the complete oracle; and
- **repair feedback shown to the LLM**, which must not contain direct oracle values.

The validator itself is not weakened or changed. Only the feedback view is sanitized.

## 2. Repair eligibility

For each model and each condition independently:

- run all Formal tasks once under C0;
- run all Formal tasks once under C1;
- only initial failures are eligible for repair;
- each failed task-condition receives exactly one repair attempt;
- initial successes are never repaired or rerun.

## 3. C0 and C1 context

### C0 repair

Repair input contains:

- original task;
- original model output;
- sanitized automated diagnostics;
- observed execution feedback if execution occurred.

It does **not** add official documentation.

### C1 repair

Repair input contains:

- the same original C1 task prompt;
- the same frozen official-document context used in C1 initial generation;
- original model output;
- sanitized automated diagnostics;
- observed execution feedback if execution occurred.

The C1 corpus itself remains frozen and is not changed based on model performance.

## 4. Allowed diagnostic information

The repair model may receive:

- parser/validator error code;
- a fixed generic public description of that error code;
- offending Spark key or submit option;
- the model's own observed value;
- the model's own observed configuration source;
- offending raw option/token/positional argument;
- failed runtime assertion name;
- actual runtime observation;
- bounded tail of actual Spark/YARN execution stderr when available.

These are diagnostic observations, not the hidden solution.

## 5. Forbidden oracle-like feedback

The repair prompt must not directly expose fields such as:

- `expected`;
- `expected_normalized`;
- `expected_type`;
- `allowed_sources`;
- accepted/valid/correct values;
- reference/gold/oracle answer fields;
- calculated runtime targets derived from task constraints.

For example, Pilot-style feedback:

```json
{
  "code": "MISSING_REQUIRED_SETTING",
  "key": "spark.executor.memoryOverhead",
  "expected": "1536"
}
```

becomes Formal V1 feedback:

```json
{
  "code": "MISSING_REQUIRED_SETTING",
  "message": "A required Spark setting is missing.",
  "key": "spark.executor.memoryOverhead"
}
```

A value mismatch such as:

```json
{
  "key": "spark.dynamicAllocation.minExecutors",
  "observed": "0",
  "expected": "1"
}
```

becomes:

```json
{
  "code": "TASK_VALUE_MISMATCH",
  "message": "A Spark setting does not satisfy the frozen task constraint.",
  "key": "spark.dynamicAllocation.minExecutors",
  "observed": "0"
}
```

The model must recover the correct value from the original task, C1 documentation
when applicable, or its own reasoning.

## 6. Runtime feedback

Only **failed** runtime assertions are returned to the repair model.

Allowed example:

```json
{
  "name": "effective::spark.executor.memory",
  "passed": false,
  "observed": "2g"
}
```

The hidden `expected` value is not returned.

For composite runtime checks, oracle-derived calculated targets are removed. For
example, `yarn_memory_allocation` may expose actual peak allocated memory but not
the internally calculated target allocation.

## 7. Implementation rule

Formal repair code must call:

`48_repair_feedback_sanitizer_v01.py`

and must never concatenate raw `parser_result.json`, `static_validation.json`, or
`runtime_validation.json` directly into a repair prompt.

The sanitizer uses a whitelist design so that future validator fields are dropped
unless explicitly approved.

## 8. Relationship to Pilot

The Pilot was a protocol-development phase. Its repair scores remain valid as
Pilot observations under the Pilot feedback design, but they are not used as
Formal repair estimates.

Do not:

- rerun Qwen Pilot repair with V1;
- rerun DeepSeek Pilot repair with V1;
- alter Pilot scores;
- tune V1 after seeing Formal model outcomes.

After the V1 audit passes, this repair-feedback protocol should be frozen before
Formal model evaluation begins.
