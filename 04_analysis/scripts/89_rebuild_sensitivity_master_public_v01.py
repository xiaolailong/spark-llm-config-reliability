#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import csv
import sys

def as_bool(v):
    return str(v).strip().lower() in {
        "true", "1", "yes", "y", "pass", "success", "succeeded"
    }

script = Path(__file__).resolve()
paper_core = script.parents[2]

master = paper_core / "04_analysis/formal_master_v01/formal_master_540_v01.csv"
ledger = paper_core / "04_analysis/sensitivity_v01/rescored_v01/unblinded_candidate_ledger_v01.csv"
reference = paper_core / "04_analysis/sensitivity_v01/rescored_v01/sensitivity_master_540_v01.csv"
output = paper_core / "04_analysis/sensitivity_v01/rescored_v01/sensitivity_master_540_rebuilt.csv"

with master.open("r", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
with ledger.open("r", encoding="utf-8-sig", newline="") as f:
    decisions = list(csv.DictReader(f))

by_key = {}
for d in decisions:
    key = (d["model"], d["condition"], d["task_id"])
    if key in by_key:
        raise SystemExit(f"Duplicate adjudication row: {key}")
    by_key[key] = d

out_rows = []
mapped = 0
flips = 0

for r in rows:
    key = (r["model"], r["condition"], r["task_id"])
    strict = as_bool(r["initial_success"])
    d = by_key.get(key)

    if strict:
        sens = True
        blind_id = ""
        case_decision = "STRICT_PASS"
        extra_json = ""
    elif d is None:
        sens = False
        blind_id = ""
        case_decision = "STRICT_FAIL_NONCANDIDATE"
        extra_json = ""
    else:
        mapped += 1
        sens = as_bool(d["sensitivity_candidate_pass"])
        blind_id = d["blind_case_id"]
        case_decision = d["case_decision"]
        extra_json = d["extra_decisions_json"]

    flip = (not strict) and sens
    flips += int(flip)

    rr = dict(r)
    rr["strict_initial_success"] = "TRUE" if strict else "FALSE"
    rr["sensitivity_initial_success"] = "TRUE" if sens else "FALSE"
    rr["sensitivity_flip_0_to_1"] = "TRUE" if flip else "FALSE"
    rr["sensitivity_blind_case_id"] = blind_id
    rr["sensitivity_case_decision"] = case_decision
    rr["sensitivity_extra_decisions_json"] = extra_json
    out_rows.append(rr)

if len(rows) != 540:
    raise SystemExit(f"Expected 540 strict-master rows, got {len(rows)}")
if mapped != 65:
    raise SystemExit(f"Expected 65 adjudicated candidate rows, got {mapped}")
if flips != 55:
    raise SystemExit(f"Expected 55 strict FAIL -> sensitivity PASS flips, got {flips}")

fieldnames = list(out_rows[0].keys())
with output.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(out_rows)

# Compare every public outcome/provenance field against the frozen reference.
with reference.open("r", encoding="utf-8-sig", newline="") as f:
    ref_rows = list(csv.DictReader(f))

if len(ref_rows) != len(out_rows):
    raise SystemExit("Frozen sensitivity master row count differs.")

ref_by_key = {
    (r["model"], r["condition"], r["task_id"]): r
    for r in ref_rows
}
check_fields = [
    "strict_initial_success",
    "sensitivity_initial_success",
    "sensitivity_flip_0_to_1",
    "sensitivity_blind_case_id",
    "sensitivity_case_decision",
    "sensitivity_extra_decisions_json",
]
mismatch = []
for r in out_rows:
    key = (r["model"], r["condition"], r["task_id"])
    rr = ref_by_key.get(key)
    if rr is None:
        mismatch.append((key, "missing reference row"))
        continue
    for c in check_fields:
        if str(r[c]) != str(rr[c]):
            mismatch.append((key, c, r[c], rr[c]))

if mismatch:
    print("Sensitivity reconstruction mismatch:", file=sys.stderr)
    for x in mismatch[:20]:
        print(x, file=sys.stderr)
    raise SystemExit(2)

print("PASS: sensitivity master reconstructed from public strict master + adjudication ledger.")
print(f"candidate rows = {mapped}")
print(f"strict FAIL -> sensitivity PASS = {flips}")
print(output)
