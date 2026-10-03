#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
78_reaudit_formal_master_v011.py

Corrected integrity audit for the sealed Formal90 master dataset.

V0.1.1 only fixes audit-code compatibility:
  1) authoritative condition labels are
     C0_closed_book / C1_frozen_official_docs_v04;
  2) authoritative parser-version column is
     parser_version_authoritative.

It does NOT change the dataset, scoring, expected outcomes, or experiment protocol.
"""

from __future__ import annotations
import csv, hashlib, json, sys
from collections import Counter
from pathlib import Path


def project_root_from_script() -> Path:
    p = Path(__file__).resolve()
    # <root>/paper_core/04_analysis/scripts/script.py
    root = p.parents[3]
    if not (root / "paper_core").exists():
        raise RuntimeError(f"Cannot infer project root from {p}")
    return root


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def b(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"true","1","yes","y","pass","success","succeeded"}:
        return True
    if s in {"false","0","no","n","fail","failure","failed"}:
        return False
    if s == "":
        return None
    raise ValueError(f"Bad boolean: {v!r}")


def model(v: str) -> str:
    s = str(v).strip().lower()
    return {"qwen":"Qwen", "deepseek":"DeepSeek", "gpt6_sol":"GPT"}.get(s, s)


def cond(v: str) -> str:
    s = str(v).strip()
    return {
        "C0_closed_book":"C0",
        "C1_frozen_official_docs_v04":"C1",
        "C0":"C0", "C1":"C1"
    }.get(s, s)


def cat(v: str) -> str:
    s = str(v).strip()
    mp = {
        "A_resource":"A", "B_driver_executor":"B", "C_yarn_deploy":"C",
        "D_source_precedence":"D", "E_dependency_interaction":"E",
        "F_integrated":"F",
    }
    return mp.get(s, s[:1].upper() if s else s)


EXPECTED_INITIAL = {
    ("Qwen","C0"):(23,90), ("Qwen","C1"):(61,90),
    ("DeepSeek","C0"):(65,90), ("DeepSeek","C1"):(79,90),
    ("GPT","C0"):(70,90), ("GPT","C1"):(68,90),
}
EXPECTED_REPAIR = {
    ("Qwen","C0"):(29,67), ("Qwen","C1"):(21,29),
    ("DeepSeek","C0"):(25,25), ("DeepSeek","C1"):(11,11),
    ("GPT","C0"):(20,20), ("GPT","C1"):(21,22),
}
EXPECTED_FINAL = {
    ("Qwen","C0"):(52,90), ("Qwen","C1"):(82,90),
    ("DeepSeek","C0"):(90,90), ("DeepSeek","C1"):(90,90),
    ("GPT","C0"):(90,90), ("GPT","C1"):(89,90),
}
EXPECTED_TRANSITIONS = {
    "Qwen":{"PASS→PASS":22,"FAIL→PASS":39,"PASS→FAIL":1,"FAIL→FAIL":28},
    "DeepSeek":{"PASS→PASS":64,"FAIL→PASS":15,"PASS→FAIL":1,"FAIL→FAIL":10},
    "GPT":{"PASS→PASS":63,"FAIL→PASS":5,"PASS→FAIL":7,"FAIL→FAIL":15},
}

root = project_root_from_script()
master = root / "paper_core/04_analysis/formal_master_v01/formal_master_540_v01.csv"
manifest = root / "paper_core/04_analysis/formal_master_v01/formal_master_manifest_v01.json"
outdir = root / "paper_core/04_analysis/formal_master_v01/audit_v011"
outdir.mkdir(parents=True, exist_ok=True)

with master.open("r", encoding="utf-8-sig", newline="") as f:
    rdr = csv.DictReader(f)
    fields = rdr.fieldnames or []
    raw = list(rdr)

required = {
    "model","condition","task_id","category","initial_success",
    "initial_failure_layer","repair_eligible","repair_success",
    "final_after_one_repair_success","parser_engineering_fix_applied",
    "parser_version_authoritative"
}
checks = []

def ck(cid, ok, expected, actual, detail=""):
    checks.append({
        "check_id": cid, "status": "PASS" if ok else "FAIL",
        "expected": str(expected), "actual": str(actual), "detail": detail
    })

missing = sorted(required - set(fields))
ck("V011_01_schema", not missing, "required columns present", missing or "all present")

rows = []
for r in raw:
    rows.append({
        "model": model(r["model"]),
        "condition": cond(r["condition"]),
        "task_id": r["task_id"].strip(),
        "category": cat(r["category"]),
        "initial_success": b(r["initial_success"]),
        "initial_failure_layer": (r["initial_failure_layer"] or "").strip().upper(),
        "repair_eligible": b(r["repair_eligible"]),
        "repair_success": b(r["repair_success"]),
        "final_success": b(r["final_after_one_repair_success"]),
        "parser_fix": b(r["parser_engineering_fix_applied"]),
        "parser_version": str(r["parser_version_authoritative"]).strip(),
        "repair_failure_layer": (r.get("repair_failure_layer") or "").strip().upper(),
        "initial_executed": b(r.get("initial_executed","")),
        "repair_executed": b(r.get("repair_executed","")),
        "app_initial": (r.get("application_id_initial") or "").strip(),
        "app_repair": (r.get("application_id_repair") or "").strip(),
    })

ck("V011_02_rows", len(rows)==540, 540, len(rows))
keys=[(r["model"],r["condition"],r["task_id"]) for r in rows]
ck("V011_03_unique_keys", len(set(keys))==540, 540, len(set(keys)))
tasks=sorted({r["task_id"] for r in rows})
ck("V011_04_tasks", len(tasks)==90, 90, len(tasks))
ck("V011_05_six_rows_per_task",
   all(sum(1 for r in rows if r["task_id"]==t)==6 for t in tasks),
   "6 per task","checked 90 tasks")

levels=sorted({r["condition"] for r in rows})
ck("V011_06_condition_normalization", levels==["C0","C1"], ["C0","C1"], levels)

cat_counts=Counter()
for t in tasks:
    cats={r["category"] for r in rows if r["task_id"]==t}
    if len(cats)==1:
        cat_counts[next(iter(cats))]+=1
ck("V011_07_categories", dict(sorted(cat_counts.items()))=={x:15 for x in "ABCDEF"},
   {x:15 for x in "ABCDEF"}, dict(sorted(cat_counts.items())))

initial={}
repair={}
final={}
for m in ["Qwen","DeepSeek","GPT"]:
    for c in ["C0","C1"]:
        rr=[r for r in rows if r["model"]==m and r["condition"]==c]
        initial[(m,c)]=(sum(r["initial_success"] is True for r in rr),len(rr))
        elig=[r for r in rr if r["repair_eligible"] is True]
        repair[(m,c)]=(sum(r["repair_success"] is True for r in elig),len(elig))
        final[(m,c)]=(sum(r["final_success"] is True for r in rr),len(rr))

ck("V011_08_initial_counts", initial==EXPECTED_INITIAL, EXPECTED_INITIAL, initial)
ck("V011_09_repair_counts", repair==EXPECTED_REPAIR, EXPECTED_REPAIR, repair)
ck("V011_10_final_counts", final==EXPECTED_FINAL, EXPECTED_FINAL, final)

trans={}
for m in ["Qwen","DeepSeek","GPT"]:
    cnt=Counter()
    for t in tasks:
        a=next(r for r in rows if r["model"]==m and r["condition"]=="C0" and r["task_id"]==t)
        z=next(r for r in rows if r["model"]==m and r["condition"]=="C1" and r["task_id"]==t)
        cnt[("PASS" if a["initial_success"] else "FAIL")+"→"+("PASS" if z["initial_success"] else "FAIL")]+=1
    trans[m]=dict(cnt)
ck("V011_11_transitions", trans==EXPECTED_TRANSITIONS, EXPECTED_TRANSITIONS, trans)

logic_elig=all(r["repair_eligible"] == (not r["initial_success"]) for r in rows)
ck("V011_12_repair_eligibility_logic", logic_elig, "eligible iff Initial failure", logic_elig)

logic_final=all(
    r["final_success"] == (r["initial_success"] or (r["repair_eligible"] and r["repair_success"] is True))
    for r in rows
)
ck("V011_13_final_logic", logic_final, "Initial success OR successful repair", logic_final)

fail_layers={
    m:Counter(r["initial_failure_layer"] for r in rows if r["model"]==m and not r["initial_success"])
    for m in ["Qwen","DeepSeek","GPT"]
}
expected_layers={
    "Qwen":Counter({"STATIC":95,"PARSE":1}),
    "DeepSeek":Counter({"STATIC":36}),
    "GPT":Counter({"STATIC":42}),
}
ck("V011_14_failure_layers", fail_layers==expected_layers, expected_layers, fail_layers)

flagged=sorted((r["model"],r["condition"],r["task_id"]) for r in rows if r["parser_fix"] is True)
expected_flagged=[("GPT","C1","F_A03"),("GPT","C1","F_A15")]
ck("V011_15_parser_fix_rows", flagged==expected_flagged, expected_flagged, flagged)
ck("V011_16_parser_version",
   all(r["parser_version"]=="0.1.1" for r in rows),
   "0.1.1 for authoritative rows",
   Counter(r["parser_version"] for r in rows))

fa03=next(r for r in rows if r["model"]=="GPT" and r["condition"]=="C1" and r["task_id"]=="F_A03")
fa15=next(r for r in rows if r["model"]=="GPT" and r["condition"]=="C1" and r["task_id"]=="F_A15")
ck("V011_17_parser_special_outcomes",
   fa03["initial_success"] is True and fa15["initial_success"] is False and fa15["initial_failure_layer"]=="STATIC",
   "F_A03 PASS; F_A15 STATIC FAIL",
   (fa03["initial_success"],fa15["initial_success"],fa15["initial_failure_layer"]))

gpt_remaining=[r for r in rows if r["model"]=="GPT" and not r["final_success"]]
ck("V011_18_gpt_remaining",
   len(gpt_remaining)==1 and gpt_remaining[0]["condition"]=="C1" and gpt_remaining[0]["task_id"]=="F_E08"
   and gpt_remaining[0]["repair_failure_layer"]=="STATIC",
   "only GPT C1/F_E08, STATIC",
   [(r["condition"],r["task_id"],r["repair_failure_layer"]) for r in gpt_remaining])

ck("V011_19_initial_runtime_evidence",
   all(r["initial_executed"] is True and r["app_initial"] for r in rows if r["initial_success"]),
   "all Initial successes executed with application ID",
   "checked")
ck("V011_20_repair_runtime_evidence",
   all(r["repair_executed"] is True and r["app_repair"] for r in rows if r["repair_success"] is True),
   "all successful repairs executed with application ID",
   "checked")

csv_hash=sha256(master)
manifest_hash=None
if manifest.exists():
    obj=json.loads(manifest.read_text(encoding="utf-8"))
    def walk(x):
        if isinstance(x,dict):
            for k,v in x.items():
                if k=="formal_master_540_v01.csv" and isinstance(v,dict):
                    for kk,vv in v.items():
                        if "sha256" in kk.lower() and isinstance(vv,str):
                            return vv
                z=walk(v)
                if z: return z
        elif isinstance(x,list):
            for v in x:
                z=walk(v)
                if z: return z
        return None
    manifest_hash=walk(obj)
    if manifest_hash is None:
        # known manifest may expose a top-level field; search every 64-hex string
        txt=manifest.read_text(encoding="utf-8")
        if csv_hash in txt:
            manifest_hash=csv_hash
ck("V011_21_manifest_hash", manifest_hash==csv_hash, csv_hash, manifest_hash)

fail=sum(c["status"]=="FAIL" for c in checks)
summary={
    "audit_version":"0.1.1",
    "input_csv":str(master),
    "input_sha256":csv_hash,
    "rows":len(rows),"unique_tasks":len(tasks),
    "pass":sum(c["status"]=="PASS" for c in checks),
    "fail":fail,
    "overall":"PASS" if fail==0 else "FAIL",
}
(outdir/"formal_master_audit_v011.json").write_text(
    json.dumps({"summary":summary,"checks":checks},ensure_ascii=False,indent=2,default=str)+"\n",
    encoding="utf-8"
)
with (outdir/"formal_master_audit_v011_checks.csv").open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.DictWriter(f,fieldnames=["check_id","status","expected","actual","detail"])
    w.writeheader(); w.writerows(checks)

lines=[
    "# Formal master dataset integrity audit V0.1.1","",
    f"- Overall: **{summary['overall']}**",
    f"- PASS: {summary['pass']}",
    f"- FAIL: {summary['fail']}",
    f"- Input SHA256: `{csv_hash}`","",
    "V0.1.1 corrects only audit-code compatibility with the authoritative condition labels "
    "and `parser_version_authoritative` column. No dataset or scoring rule was changed.","",
    "| Check | Status |","|---|---|"
]
for c in checks:
    lines.append(f"| {c['check_id']} | **{c['status']}** |")
(outdir/"formal_master_audit_v011_report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

print("="*80)
print(f"Formal master re-audit V0.1.1: {summary['overall']}")
print(f"PASS={summary['pass']} FAIL={summary['fail']}")
print(f"CSV SHA256={csv_hash}")
print(f"Output={outdir}")
print("="*80)
sys.exit(0 if fail==0 else 2)
