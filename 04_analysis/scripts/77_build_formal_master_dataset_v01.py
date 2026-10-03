#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Build the authoritative 540-row Formal master dataset.

ZERO model/API calls.
ZERO Spark/YARN runs.

Inputs are the already sealed local result directories.
Outputs:
- formal_master_540_v01.csv
- formal_master_540_v01.json
- formal_summary_v01.json
- formal_summary_tables_v01.md
- formal_master_manifest_v01.json

No source result file is modified.
"""

from __future__ import annotations
import csv, hashlib, json, os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

MODELS = {
 "qwen": {
   "initial": Path("runs/qwen_formal_initial_v01/20260930T101751Z_fe995b04/qwen_formal_initial_summary.json"),
   "repair": Path("runs/qwen_formal_repair_once_v01/20260930T132312Z_62a763de/qwen_formal_repair_once_summary.json"),
   "label": "Qwen3.5-9B",
 },
 "deepseek": {
   "initial": Path("runs/deepseek_formal_initial_v01/20260930T154824Z_58fc84cb/deepseek_formal_initial_summary.json"),
   "repair": Path("runs/deepseek_formal_repair_once_v01/20261001T020000Z_20d6f564/deepseek_formal_repair_once_summary.json"),
   "label": "DeepSeek Flash",
 },
 "gpt6_sol": {
   "initial": Path("runs/gpt6_sol_parserfix_revalidation_v011/20261001T044711Z_d1b568c4/gpt6_sol_formal_initial_parserfix_overlay_v011.json"),
   "repair": Path("runs/gpt6_sol_formal_repair_once_v01/20261001T050501Z_d7ea02dc/gpt6_sol_formal_repair_once_summary.json"),
   "label": "GPT-6 Sol",
 },
}

CATALOG=Path("fixtures/formal_tasks_90_frozen_v01.json")
EXPECTED_CATALOG_SHA="b82814319214c59f3d17edb54de712e8bd48304085ca619065df01c1d7c013a8"

def readj(p): return json.loads(p.read_text(encoding="utf-8"))
def sha(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()
def utc(): return datetime.now(timezone.utc).isoformat()

def val(row,*names):
    for n in names:
        if n in row: return row.get(n)
    return None

def main():
    root=Path(__file__).resolve().parent
    if not (root/CATALOG).is_file(): raise FileNotFoundError(root/CATALOG)
    if sha(root/CATALOG)!=EXPECTED_CATALOG_SHA: raise RuntimeError("Formal catalog hash mismatch")
    catalog=readj(root/CATALOG)
    tasks={t["task_id"]:t for t in catalog["tasks"]}
    if len(tasks)!=90: raise RuntimeError("Expected 90 Formal tasks")

    source_hashes={}
    rows=[]
    for model,info in MODELS.items():
        ip=root/info["initial"]; rp=root/info["repair"]
        if not ip.is_file(): raise FileNotFoundError(ip)
        if not rp.is_file(): raise FileNotFoundError(rp)
        source_hashes[str(info["initial"])]=sha(ip)
        source_hashes[str(info["repair"])]=sha(rp)
        ini=readj(ip); rep=readj(rp)
        irows=ini["results"]; rrows=rep["results"]
        if len(irows)!=180: raise RuntimeError(f"{model}: Initial rows !=180")
        rmap={(r["condition"],r["task_id"]):r for r in rrows}

        for ir in irows:
            tid=ir["task_id"]; cond=ir["condition"]; task=tasks[tid]
            rr=rmap.get((cond,tid))
            initial_success=bool(ir.get("initial_success"))
            repair_eligible=not initial_success
            if repair_eligible and rr is None:
                raise RuntimeError(f"{model}/{cond}/{tid}: missing repair result")
            if initial_success and rr is not None:
                raise RuntimeError(f"{model}/{cond}/{tid}: successful Initial unexpectedly repaired")

            final_success = initial_success or bool(rr and rr.get("repair_success"))
            rule_ids=task.get("rule_ids") or []
            source_case_ids=task.get("source_case_ids") or task.get("source_ids") or []
            provenance_type=task.get("provenance_type") or task.get("provenance")

            row={
              "model":model,
              "model_label":info["label"],
              "condition":cond,
              "task_id":tid,
              "category":task.get("category"),
              "rule_ids":";".join(rule_ids),
              "provenance_type":provenance_type if not isinstance(provenance_type,(dict,list)) else json.dumps(provenance_type,ensure_ascii=False,sort_keys=True),
              "source_case_ids":";".join(source_case_ids) if isinstance(source_case_ids,list) else (source_case_ids or ""),

              "initial_response_format_valid":ir.get("response_format_valid"),
              "initial_parse_status":ir.get("parse_status"),
              "initial_static_status":ir.get("static_status"),
              "initial_executed":ir.get("executed"),
              "initial_runtime_status":ir.get("runtime_status"),
              "initial_success":initial_success,
              "initial_failure_layer":ir.get("failure_layer"),
              "initial_error_codes":";".join(ir.get("validator_error_codes") or []),
              "application_id_initial":ir.get("application_id"),

              "repair_eligible":repair_eligible,
              "repair_success":None if rr is None else bool(rr.get("repair_success")),
              "repair_failure_layer":None if rr is None else rr.get("final_failure_layer"),
              "repair_executed":None if rr is None else rr.get("executed"),
              "repair_runtime_status":None if rr is None else rr.get("runtime_status"),
              "repair_error_codes":"" if rr is None else ";".join(rr.get("repair_validator_error_codes") or []),
              "application_id_repair":None if rr is None else rr.get("application_id"),

              "final_after_one_repair_success":final_success,
              "initial_batch_id":ini.get("batch_id") or ini.get("source_batch_id"),
              "repair_batch_id":rep.get("batch_id"),
              "parser_engineering_fix_applied":bool(ir.get("parser_engineering_fix_v011")),
              "parser_version_authoritative":"0.1.1",
            }
            rows.append(row)

    if len(rows)!=540: raise RuntimeError(f"Expected 540 master rows, got {len(rows)}")
    # uniqueness
    keys=[(r["model"],r["condition"],r["task_id"]) for r in rows]
    if len(set(keys))!=540: raise RuntimeError("Duplicate master keys")

    expected={
      ("qwen","C0_closed_book"):(23,52),
      ("qwen","C1_frozen_official_docs_v04"):(61,82),
      ("deepseek","C0_closed_book"):(65,90),
      ("deepseek","C1_frozen_official_docs_v04"):(79,90),
      ("gpt6_sol","C0_closed_book"):(70,90),
      ("gpt6_sol","C1_frozen_official_docs_v04"):(68,89),
    }
    checks={}
    for key,(ei,ef) in expected.items():
        rr=[r for r in rows if (r["model"],r["condition"])==key]
        oi=sum(r["initial_success"] for r in rr)
        of=sum(r["final_after_one_repair_success"] for r in rr)
        checks[str(key)]={"initial":oi,"final":of}
        if (oi,of)!=(ei,ef): raise RuntimeError(f"Authoritative total mismatch {key}: {(oi,of)} != {(ei,ef)}")

    out=root/"results"/"formal_master_v01"
    out.mkdir(parents=True,exist_ok=True)
    csvp=out/"formal_master_540_v01.csv"
    jsonp=out/"formal_master_540_v01.json"

    fields=list(rows[0].keys())
    with csvp.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    jsonp.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    # summaries
    summary={"schema_version":"1","created_at_utc":utc(),"row_count":540,"checks":checks,"by_model_condition":{},"paired_transitions":{}}
    for model in MODELS:
        summary["by_model_condition"][model]={}
        for cond in ["C0_closed_book","C1_frozen_official_docs_v04"]:
            rr=[r for r in rows if r["model"]==model and r["condition"]==cond]
            ini=sum(r["initial_success"] for r in rr)
            elig=sum(r["repair_eligible"] for r in rr)
            rec=sum(1 for r in rr if r["repair_success"] is True)
            fin=sum(r["final_after_one_repair_success"] for r in rr)
            summary["by_model_condition"][model][cond]={
              "initial_success":ini,"initial_total":90,"initial_rate":ini/90,
              "repair_eligible":elig,"repair_recovered":rec,
              "repair_recovery_rate":None if elig==0 else rec/elig,
              "final_success":fin,"final_total":90,"final_rate":fin/90,
            }
        trans=Counter()
        for tid in tasks:
            c0=next(r for r in rows if r["model"]==model and r["condition"]=="C0_closed_book" and r["task_id"]==tid)
            c1=next(r for r in rows if r["model"]==model and r["condition"]=="C1_frozen_official_docs_v04" and r["task_id"]==tid)
            trans[f"{'PASS' if c0['initial_success'] else 'FAIL'}→{'PASS' if c1['initial_success'] else 'FAIL'}"]+=1
        summary["paired_transitions"][model]=dict(trans)

    sp=out/"formal_summary_v01.json"
    sp.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    md=[]
    md.append("# Formal90 三模型权威结果摘要\n")
    md.append("| Model | Condition | Initial | Repair recovery | Final |\n|---|---|---:|---:|---:|")
    for model in ["qwen","deepseek","gpt6_sol"]:
        for cond in ["C0_closed_book","C1_frozen_official_docs_v04"]:
            d=summary["by_model_condition"][model][cond]
            md.append(f"| {MODELS[model]['label']} | {'C0' if cond=='C0_closed_book' else 'C1'} | {d['initial_success']}/90 ({d['initial_rate']:.1%}) | {d['repair_recovered']}/{d['repair_eligible']} ({d['repair_recovery_rate']:.1%}) | {d['final_success']}/90 ({d['final_rate']:.1%}) |")
    md.append("\n## C0→C1 Initial paired transitions\n")
    for model in ["qwen","deepseek","gpt6_sol"]:
        md.append(f"- {MODELS[model]['label']}: {summary['paired_transitions'][model]}")
    (out/"formal_summary_tables_v01.md").write_text("\n".join(md)+"\n",encoding="utf-8")

    manifest={
      "schema_version":"1","created_at_utc":utc(),
      "catalog_sha256":EXPECTED_CATALOG_SHA,
      "source_hashes":source_hashes,
      "outputs":{
        "formal_master_540_v01.csv":sha(csvp),
        "formal_master_540_v01.json":sha(jsonp),
        "formal_summary_v01.json":sha(sp),
        "formal_summary_tables_v01.md":sha(out/"formal_summary_tables_v01.md"),
      },
      "authoritative_checks":checks,
    }
    mp=out/"formal_master_manifest_v01.json"
    mp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    print("="*78)
    print("Formal Master Dataset V1: COMPLETE")
    print("="*78)
    print("Rows             : 540")
    print("Unique keys      : 540")
    for k,v in checks.items(): print(f"{k}: initial={v['initial']}/90 final={v['final']}/90")
    print(f"Output           : {out}")
    print("Model/API calls  : 0")
    print("Spark/YARN runs  : 0")
    print("="*78)

if __name__=="__main__":
    main()
