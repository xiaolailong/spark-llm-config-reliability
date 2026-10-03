# Deterministic Parser V0.1.1 Engineering Fix

## Why this is an engineering bug

The frozen V0.1 parser design contract stated that it supports:

`1) spark-submit command/options`

The Formal prompt also states:

`You may include the literal spark-submit followed only by Spark submission options.`

Therefore a response such as:

`--conf spark.executor.cores=3`

is a permitted representation of Spark submission options. V0.1 failed to
extract it solely because artifact extraction required the literal
`spark-submit` token.

## Fix

V0.1.1 recognizes a bare sequence of `--...` Spark submission options and feeds
that sequence into the existing deterministic spark-submit option parser.

It does not:
- invent settings;
- normalize a wrong Spark semantic into a right one;
- change the validator;
- relax unauthorized-setting rules;
- call any model.

Old SHA256:
`82534e2f7a3d2ba8347a71ccbbf1c3df7ec973000093a726aa61f899714e2685`

New SHA256:
`0237a71d185cf4fe9f3bdc05d8088d244debfc84c258444a4b454f255b4f49db`

## Cross-model regression audit requirement

Reparse all stored Formal raw responses:
- Qwen Initial 180
- Qwen Repair 96
- DeepSeek Initial 180
- DeepSeek Repair 36
- GPT Initial 180

Total = 672.

Expected semantic parse differences: exactly 2:
- GPT C1 / F_A03
- GPT C1 / F_A15

All other 670 must remain unchanged.

## Revalidation

F_A03 must proceed through the same real Spark/YARN execution chain because it
becomes Static PASS under V0.1.1.

F_A15 remains Static FAIL because it contains an unauthorized
`spark.dynamicAllocation.enabled=false`; it must not be executed.

The original GPT responses are reused byte-for-byte and no OpenAI request is
made.
