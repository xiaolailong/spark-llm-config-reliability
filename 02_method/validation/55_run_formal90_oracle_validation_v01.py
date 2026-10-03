#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
55_run_formal90_oracle_validation_v01.py

Known-good oracle real-execution validation for Formal 90 Draft V0.1.

No LLM calls.

Requirements:
- latest Formal90 lint must PASS and match the current catalog SHA256;
- frozen parser / validator V0.2 / builder are reused unchanged;
- all 90 oracle artifacts are parsed, statically validated, safely built, and
  really executed on the frozen Spark 3.5.9 / YARN P0 environment;
- R09 tasks use a dedicated safe runner fixture:
    model property lines -> isolated per-run custom properties file
    runner -> fixed --properties-file <that local file>
  The model/oracle never controls the file path.
- batch supports resume; completed task results are not rerun.
"""

from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, os, shlex, subprocess, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

CATALOG_REL=Path("fixtures/formal_tasks_90_draft_v01.json")
PROFILE_REL=Path("fixtures/validator_profile_v02.json")

EXPECTED_COMPONENT_HASHES={
    "09_deterministic_parser.py":"82534e2f7a3d2ba8347a71ccbbf1c3df7ec973000093a726aa61f899714e2685",
    "13_safe_command_builder.py":"1b9ed472ecf03a565d5a37f44dfb895cf183ceb52a49afbc0b2cf8a6ac26f6b3",
    "23_static_validator_v02.py":"db62661ac2a511b903e84910c57894d5cc3a6dcca47649768e53cf653f1d0727",
    "24_run_pilot30_oracle_validation.py":"1993c966b3d5c24349b152aa7bc721ee1b0815676b1509d0fd9f08ba16082284",
    "fixtures/validator_profile_v02.json":"0783f4a9c5f5abcd8693a31fb01fccecff5d5170db44143bf039b88b1f2f3afc",
}

REQUIRED_BASE_FUNCTIONS={
    "assertion","copy_conf_dir","write_spark_defaults","fixed_submit_args","probe_mode",
    "run_cmd","find_app_id","fetch_yarn_app","poll_yarn_peak","poll_yarn_final","hdfs_cat","poll_history",
    "semantic_equal","memory_to_mib","parse_am_requested_mb","read_yarn_scheduler","round_alloc"
}

def utc_iso(): return datetime.now(timezone.utc).isoformat()
def utc_compact(): return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def read_json(p:Path): return json.loads(p.read_text(encoding="utf-8"))
def write_json(p:Path,obj:Any):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def write_text(p:Path,s:str):
    p.parent.mkdir(parents=True,exist_ok=True); p.write_text(s,encoding="utf-8")
def sha256_file(p:Path):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()
def load_module(p:Path,name:str):
    spec=importlib.util.spec_from_file_location(name,p)
    if spec is None or spec.loader is None: raise RuntimeError(f"Cannot import {p}")
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def find_latest_lint(root:Path)->Path:
    cands=sorted(
        [p for p in (root/"results").glob("formal90_draft_v01_lint_*") if p.is_dir()],
        key=lambda p:p.name, reverse=True
    )
    if not cands: raise FileNotFoundError("No formal90_draft_v01_lint_* result found. Run 54_lint_formal90_draft_v01.py first.")
    return cands[0]/"formal90_lint_report.json"

def verify_preconditions(root:Path)->Dict[str,Any]:
    observed={}
    # All execution-chain components used by this Formal oracle runner are hash-locked.
    for rel,exp in EXPECTED_COMPONENT_HASHES.items():
        p=root/rel
        if not p.is_file(): raise FileNotFoundError(p)
        got=sha256_file(p); observed[rel]=got
        if got!=exp:
            raise RuntimeError(f"Frozen component hash mismatch: {rel}\nexpected={exp}\nobserved={got}")
    lint_path=find_latest_lint(root)
    lint=read_json(lint_path)
    if lint.get("pass") is not True:
        raise RuntimeError(f"Latest Formal90 lint is not PASS: {lint_path}")
    catalog_hash=sha256_file(root/CATALOG_REL)
    if lint.get("catalog_sha256")!=catalog_hash:
        raise RuntimeError("Formal catalog changed after lint; rerun 54_lint_formal90_draft_v01.py")
    if lint.get("oracle_static_pass")!=90:
        raise RuntimeError("Static oracle chain is not 90/90 PASS")
    return {"observed_hashes":observed,"lint_path":str(lint_path.relative_to(root)),"lint_sha256":sha256_file(lint_path),"catalog_sha256":catalog_hash}

def write_custom_properties(path:Path,event_defaults:Dict[str,str],model_defaults:list[dict[str,str]]):
    props=dict(event_defaults)
    for item in model_defaults: props[item["key"]]=item["value"]
    lines=["# Formal90 R09 isolated local --properties-file"]
    for k in sorted(props): lines.append(f"{k} {props[k]}")
    lines.append("")
    write_text(path,"\n".join(lines))

def poll_yarn_peak_for_expected_containers(base, rm_base:str, app_id:str, timeout_seconds:int, expected_containers:int)->Dict[str,Any]:
    """Observe live YARN allocation until AM + expected executors are visible when possible."""
    deadline=time.time()+timeout_seconds
    obs=[]; peak_mb=0; peak_containers=0
    target=max(2,int(expected_containers))
    while time.time()<deadline:
        app=base.fetch_yarn_app(rm_base,app_id)
        if app is not None:
            mb=int(app.get("allocatedMB") or 0); ct=int(app.get("runningContainers") or 0)
            peak_mb=max(peak_mb,mb); peak_containers=max(peak_containers,ct)
            obs.append({"ts_utc":utc_iso(),"state":app.get("state"),"allocatedMB":mb,"runningContainers":ct})
            if ct>=target and mb>0:
                time.sleep(1)
                app2=base.fetch_yarn_app(rm_base,app_id)
                if app2 is not None:
                    mb2=int(app2.get("allocatedMB") or 0); ct2=int(app2.get("runningContainers") or 0)
                    peak_mb=max(peak_mb,mb2); peak_containers=max(peak_containers,ct2)
                    obs.append({"ts_utc":utc_iso(),"state":app2.get("state"),"allocatedMB":mb2,"runningContainers":ct2})
                break
        time.sleep(1)
    return {
        "peak_allocated_mb":peak_mb,
        "peak_running_containers":peak_containers,
        "expected_running_containers":target,
        "observations":obs,
    }

def execute_task(
    base, parser_mod, validator_mod, builder_mod, profile, task,
    root:Path, tdir:Path, spark_home:Path, hadoop_home:Path,
    rm_base:str, history_base:str, submit_timeout:int, yarn_timeout:int, history_timeout:int,
    scheduler:Dict[str,Any], batch_id:str
)->Dict[str,Any]:
    checks=[]
    app_id=None
    pr=parser_mod.parse_response(task["oracle_artifact"])
    write_json(tdir/"parser_result.json",pr)
    vr=validator_mod.validate(pr,task["task_constraints"],profile)
    write_json(tdir/"static_validation.json",vr)
    checks.append(base.assertion("parse_status",pr.get("parse_status")=="OK",pr.get("parse_status"),"OK"))
    checks.append(base.assertion("static_status",vr.get("static_status")=="PASS",vr.get("static_status"),"PASS"))
    if not all(x["passed"] for x in checks):
        return {"task_success":False,"failure_layer":"STATIC_OR_PARSE","application_id":None,"assertions":checks}

    plan=builder_mod.build_execution_plan(pr,vr)
    write_json(tdir/"builder_plan.json",plan)

    conf_dir=tdir/"spark-conf"
    base.copy_conf_dir(spark_home,conf_dir)
    event_defaults={
        "spark.eventLog.enabled":"true",
        "spark.eventLog.dir":"hdfs://chenll:9000/spark-history",
    }
    fixture=(task.get("fixture") or {})
    fixture_defaults=fixture.get("spark_defaults") or {}
    custom_properties_file=None

    if fixture.get("model_properties_destination")=="custom_properties_file":
        # Keep instrumentation in both the isolated default file and the custom file.
        base.write_spark_defaults(conf_dir/"spark-defaults.conf",event_defaults,fixture_defaults,[])
        custom_properties_file=tdir/"formal90-custom.properties"
        write_custom_properties(custom_properties_file,event_defaults,plan.get("model_spark_defaults") or [])
    else:
        base.write_spark_defaults(
            conf_dir/"spark-defaults.conf",
            event_defaults,fixture_defaults,plan.get("model_spark_defaults") or []
        )

    fixed=base.fixed_submit_args(task,pr,task["runtime_assertion_profile"])
    if custom_properties_file is not None:
        fixed += ["--properties-file",str(custom_properties_file)]

    app_name=f"FORMAL90-ORACLE-{task['task_id']}-{batch_id}"
    output_dir=f"hdfs:///tmp/spark-llm/formal90-oracle/{batch_id}/{task['task_id']}"
    output_file=f"{output_dir}/effective_conf.json"
    mode=base.probe_mode(task,pr)

    jar=root/"config_probe_v02/build/config-probe-v0.2.jar"
    cmd=[
        str(spark_home/"bin"/"spark-submit"),
        "--class","edu.jiageng.sparkllm.ConfigProbeV02",
        "--name",app_name,
        *fixed,
        *(plan.get("model_submit_args") or []),
        str(jar),output_dir,*mode
    ]
    env=os.environ.copy()
    env["SPARK_CONF_DIR"]=str(conf_dir)
    hconf=Path(os.environ.get("HADOOP_CONF_DIR",str(hadoop_home/"etc"/"hadoop"))).resolve()
    env.setdefault("HADOOP_CONF_DIR",str(hconf))
    env.pop("SPARK_DRIVER_MEMORY",None)

    write_text(tdir/"command.txt",shlex.join(cmd)+"\n")
    write_json(tdir/"execution_plan.json",{
        "fixed_submit_args":fixed,
        "builder_plan":plan,
        "custom_properties_file":None if custom_properties_file is None else str(custom_properties_file),
        "final_argv":cmd,
        "probe_mode":mode,
    })

    submit_start_ms=int(time.time()*1000)
    timed=False
    try:
        cp=base.run_cmd(cmd,env,submit_timeout)
        rc=cp.returncode; stdout=cp.stdout; stderr=cp.stderr
    except subprocess.TimeoutExpired as exc:
        timed=True; rc=None; stdout=exc.stdout or ""; stderr=exc.stderr or ""
        if isinstance(stdout,bytes): stdout=stdout.decode("utf-8",errors="replace")
        if isinstance(stderr,bytes): stderr=stderr.decode("utf-8",errors="replace")
    submit_end_ms=int(time.time()*1000)
    write_text(tdir/"stdout.log",stdout); write_text(tdir/"stderr.log",stderr)
    app_id=base.find_app_id(stdout,stderr)
    checks += [
        base.assertion("submit_not_timeout",not timed,timed,False),
        base.assertion("submit_rc_zero",rc==0,rc,0),
        base.assertion("application_id",app_id is not None,app_id,"application_<...>"),
    ]

    rule_ids=set(task.get("rule_ids") or [])
    required_exact=task["task_constraints"]["required_exact"]

    peak=None
    if app_id and "R08_EXECUTOR_MEMORY_OVERHEAD" in rule_ids:
        executor_count=int(required_exact.get("spark.executor.instances") or 1)
        # YARN cluster-mode application has one AM container plus executor containers.
        peak=poll_yarn_peak_for_expected_containers(
            base,rm_base,app_id,20,executor_count+1
        )
    write_json(tdir/"yarn_peak_resources.json",peak)

    yarn_app=None; yarn_obs=[]
    if app_id: yarn_app,yarn_obs=base.poll_yarn_final(rm_base,app_id,yarn_timeout)
    write_json(tdir/"yarn_state.json",{"application_id":app_id,"final_app":yarn_app,"poll_observations":yarn_obs})

    hdfs_bin=hadoop_home/"bin"/"hdfs"
    raw,hmeta=base.hdfs_cat(hdfs_bin,output_file,env)
    write_json(tdir/"hdfs_probe_fetch.json",hmeta)
    probe=None
    if raw is not None:
        write_text(tdir/"effective_conf.json",raw)
        probe=json.loads(raw)

    hist=None; hist_obs=[]
    if app_id: hist,hist_obs=base.poll_history(history_base,app_id,history_timeout)
    write_json(tdir/"history_application.json",{"application_id":app_id,"application":hist,"poll_observations":hist_obs})

    checks += [
        base.assertion("probe_json",probe is not None,output_file if probe else None,output_file),
        base.assertion("yarn_succeeded",yarn_app is not None and yarn_app.get("finalStatus")=="SUCCEEDED",
                       None if yarn_app is None else yarn_app.get("finalStatus"),"SUCCEEDED"),
        base.assertion("history_completed",hist is not None,hist is not None,True),
    ]

    if probe is not None:
        eff=probe.get("effective_conf") or {}
        for key,expected in task["task_constraints"]["required_exact"].items():
            checks.append(base.assertion(
                f"effective::{key}",
                base.semantic_equal(validator_mod,profile,key,eff.get(key),expected),
                eff.get(key),expected
            ))
        if "R07_CLIENT_DRIVER_MEMORY_PLACEMENT" in rule_ids:
            requested=base.memory_to_mib(required_exact.get("spark.driver.memory"))
            observed=probe.get("driver_max_memory_mib")
            ok=isinstance(observed,(int,float)) and requested and requested*.80<=observed<=requested*1.05
            checks.append(base.assertion("client_driver_actual_heap",ok,observed,{"requested_mib":requested}))
        if "R10_DYNAMIC_SCALE_RUNTIME" in rule_ids:
            peak_exec=probe.get("dynamic_peak_non_driver_executors")
            idle_exec=probe.get("dynamic_after_idle_non_driver_executors")
            checks.append(base.assertion("dynamic_scale_up",isinstance(peak_exec,int) and peak_exec>=2,peak_exec,">=2"))
            checks.append(base.assertion("dynamic_scale_down",isinstance(idle_exec,int) and idle_exec<=1,idle_exec,"<=1"))
        if "R11_DYNAMIC_INITIAL_PRECEDENCE" in rule_ids:
            inst=required_exact.get("spark.executor.instances")
            initial=required_exact.get("spark.dynamicAllocation.initialExecutors")
            expected=max(int(inst or 0),int(initial or 0))
            observed=probe.get("observed_max_non_driver_executors")
            checks.append(base.assertion("dynamic_initial_executor_count",isinstance(observed,int) and observed>=expected,observed,f">={expected}"))

    # Queue intent is always checked against YARN RM, regardless of which other
    # runtime behavior the task also exercises.
    if yarn_app is not None and "spark.yarn.queue" in required_exact:
        expected_queue=required_exact.get("spark.yarn.queue")
        checks.append(base.assertion("yarn_queue",yarn_app.get("queue")==expected_queue,yarn_app.get("queue"),expected_queue))

    # waitAppCompletion is a process/runtime semantic, not merely a SparkConf value.
    if yarn_app is not None and "spark.yarn.submit.waitAppCompletion" in required_exact:
        finished=int(yarn_app.get("finishedTime") or 0)
        wanted=str(required_exact.get("spark.yarn.submit.waitAppCompletion")).lower()
        if wanted=="false":
            checks.append(base.assertion(
                "submit_returned_before_yarn_finish",
                finished>0 and submit_end_ms<finished,
                {"submit_end_ms":submit_end_ms,"yarn_finished_ms":finished},
                "submit_end_ms < yarn_finished_ms"
            ))
        elif wanted=="true":
            checks.append(base.assertion(
                "submit_waited_until_yarn_finish",
                finished>0 and submit_end_ms>=finished,
                {"submit_end_ms":submit_end_ms,"yarn_finished_ms":finished},
                "submit_end_ms >= yarn_finished_ms"
            ))

    if "R08_EXECUTOR_MEMORY_OVERHEAD" in rule_ids and peak is not None:
        req=required_exact
        heap=base.memory_to_mib(req.get("spark.executor.memory"))
        overhead=base.memory_to_mib(req.get("spark.executor.memoryOverhead"),bare_is_mib=True)
        am_requested=base.parse_am_requested_mb(stderr)
        minimum=scheduler["minimum_allocation_mb"]["value"]
        increment=scheduler["increment_allocation_mb"]["value"]
        executor_count=int(req.get("spark.executor.instances") or 1)
        calculated=None
        executor_alloc=None
        am_alloc=None
        if heap is not None and overhead is not None and am_requested is not None:
            am_alloc=base.round_alloc(int(am_requested),minimum,increment)
            executor_alloc=base.round_alloc(int(heap+overhead),minimum,increment)
            calculated=am_alloc + executor_count*executor_alloc
        observed=None if peak is None else peak.get("peak_allocated_mb")
        observed_containers=None if peak is None else peak.get("peak_running_containers")
        checks.append(base.assertion(
            "yarn_memory_allocation",
            calculated is not None and observed is not None and int(observed)>=int(calculated)
                and observed_containers is not None and int(observed_containers)>=executor_count+1,
            {"peak_allocated_mb":observed,"peak_running_containers":observed_containers,
             "calculated_total_mb":calculated,"executor_count":executor_count,
             "executor_allocation_mb":executor_alloc,"am_allocation_mb":am_alloc,
             "heap_mib":heap,"overhead_mib":overhead,"am_requested_mb":am_requested},
            "peak must include rounded AM plus all requested executor containers"
        ))

    passed=all(x["passed"] for x in checks)
    return {
        "task_success":passed,
        "failure_layer":None if passed else "RUNTIME",
        "application_id":app_id,
        "assertions":checks,
        "failed_assertions":[x["name"] for x in checks if not x["passed"]],
    }

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--project-root",default=None)
    ap.add_argument("--only",help="Comma-separated Formal task IDs")
    ap.add_argument("--spark-home",default=os.environ.get("SPARK_HOME","/home/chenll/spark"))
    ap.add_argument("--hadoop-home",default=os.environ.get("HADOOP_HOME","/home/chenll/hadoop"))
    ap.add_argument("--rm-base",default="http://localhost:8088")
    ap.add_argument("--history-base",default="http://localhost:18080")
    ap.add_argument("--submit-timeout",type=int,default=600)
    ap.add_argument("--yarn-timeout",type=int,default=180)
    ap.add_argument("--history-timeout",type=int,default=180)
    args=ap.parse_args()

    root=Path(args.project_root).expanduser().resolve() if args.project_root else Path(__file__).resolve().parent
    pre=verify_preconditions(root)

    base=load_module(root/"24_run_pilot30_oracle_validation.py","formal90_oracle_base")
    missing=sorted(REQUIRED_BASE_FUNCTIONS-set(dir(base)))
    if missing: raise RuntimeError(f"24_* helper surface missing: {missing}")
    parser_mod=load_module(root/"09_deterministic_parser.py","formal90_oracle_parser")
    validator_mod=load_module(root/"23_static_validator_v02.py","formal90_oracle_validator")
    builder_mod=load_module(root/"13_safe_command_builder.py","formal90_oracle_builder")
    profile=read_json(root/PROFILE_REL)
    catalog=read_json(root/CATALOG_REL)
    tasks=catalog["tasks"]

    if args.only:
        wanted={x.strip() for x in args.only.split(",") if x.strip()}
        known={t["task_id"] for t in tasks}
        unknown=sorted(wanted-known)
        if unknown: raise ValueError(f"Unknown Formal task IDs: {unknown}")
        tasks=[t for t in tasks if t["task_id"] in wanted]

    spark_home=Path(args.spark_home).resolve()
    hadoop_home=Path(args.hadoop_home).resolve()
    for p,label in [
        (spark_home/"bin"/"spark-submit","spark-submit"),
        (hadoop_home/"bin"/"hdfs","hdfs"),
        (root/"config_probe_v02/build/config-probe-v0.2.jar","ConfigProbe V0.2 jar"),
    ]:
        if not p.is_file(): raise FileNotFoundError(f"{label} not found: {p}")

    hconf=Path(os.environ.get("HADOOP_CONF_DIR",str(hadoop_home/"etc"/"hadoop"))).resolve()
    scheduler=base.read_yarn_scheduler(hconf)

    broot=root/"runs"/"formal90_oracle_v01"
    broot.mkdir(parents=True,exist_ok=True)
    active_path=broot/"active_batch.json"
    catalog_hash=pre["catalog_sha256"]

    if active_path.exists():
        active=read_json(active_path)
        if active.get("catalog_sha256")!=catalog_hash:
            raise RuntimeError("Active oracle batch belongs to a different Formal catalog hash")
        batch_id=active["batch_id"]; batch_dir=broot/batch_id
        mode="RESUME"
    else:
        batch_id=f"{utc_compact()}_{uuid.uuid4().hex[:8]}"
        batch_dir=broot/batch_id; batch_dir.mkdir(parents=True,exist_ok=False)
        write_json(active_path,{
            "batch_id":batch_id,"catalog_sha256":catalog_hash,
            "status":"ACTIVE","created_at_utc":utc_iso()
        })
        mode="NEW BATCH"
        write_json(batch_dir/"preconditions.json",pre)
        write_json(batch_dir/"yarn_scheduler_profile.json",scheduler)

    print("="*78)
    print(f"Formal90 oracle real-execution batch: {batch_id}")
    print(f"mode={mode}  tasks={len(tasks)}  NO LLM CALLS")
    print("="*78)

    results=[]
    for idx,task in enumerate(tasks,1):
        tid=task["task_id"]; tdir=batch_dir/tid; tdir.mkdir(parents=True,exist_ok=True)
        rpath=tdir/"result.json"
        if rpath.exists():
            r=read_json(rpath); results.append(r)
            print(f"[{idx:02d}/{len(tasks)}] {tid}: SKIP existing {'PASS' if r.get('task_success') else 'FAIL'}",flush=True)
            continue
        write_json(tdir/"task.json",task)
        write_text(tdir/"oracle_artifact.txt",task["oracle_artifact"]+"\n")
        try:
            rr=execute_task(
                base,parser_mod,validator_mod,builder_mod,profile,task,root,tdir,
                spark_home,hadoop_home,args.rm_base,args.history_base,
                args.submit_timeout,args.yarn_timeout,args.history_timeout,scheduler,batch_id
            )
            result={
                "task_id":tid,"category":task["category"],
                "task_success":bool(rr["task_success"]),
                "infrastructure_error":False,
                "failure_layer":rr.get("failure_layer"),
                "application_id":rr.get("application_id"),
                "failed_assertions":rr.get("failed_assertions",[]),
                "runtime_assertion_profile":task["runtime_assertion_profile"],
                "provenance_type":task["provenance_type"],
                "primary_source_case_id":task.get("primary_source_case_id"),
            }
        except Exception as exc:
            write_text(tdir/"runner_exception.txt",repr(exc)+"\n")
            result={
                "task_id":tid,"category":task["category"],
                "task_success":False,"infrastructure_error":True,
                "failure_layer":"RUNNER_OR_INFRASTRUCTURE",
                "runner_exception":repr(exc),
                "runtime_assertion_profile":task["runtime_assertion_profile"],
            }
        write_json(rpath,result); results.append(result)
        print(f"[{idx:02d}/{len(tasks)}] {tid}: {'PASS' if result['task_success'] else 'FAIL'}",flush=True)

    # For full-catalog run, reconstruct all 90 results from disk.
    if not args.only:
        results=[]
        for task in catalog["tasks"]:
            p=batch_dir/task["task_id"]/"result.json"
            if p.exists(): results.append(read_json(p))

    bycat={}
    for cat in sorted({x["category"] for x in results}):
        rr=[x for x in results if x["category"]==cat]
        bycat[cat]={
            "pass":sum(1 for x in rr if x.get("task_success")),
            "fail":sum(1 for x in rr if not x.get("task_success")),
            "infra":sum(1 for x in rr if x.get("infrastructure_error")),
            "total":len(rr)
        }
    passed=sum(1 for x in results if x.get("task_success"))
    infra=sum(1 for x in results if x.get("infrastructure_error"))
    complete=(not args.only and len(results)==90 and passed==90 and infra==0)
    summary={
        "schema_version":"1","stage":"FORMAL90_ORACLE_REAL_EXECUTION_V01",
        "batch_id":batch_id,"catalog_sha256":catalog_hash,
        "task_count_observed":len(results),"pass_count":passed,
        "fail_count":len(results)-passed,"infrastructure_error_count":infra,
        "all_90_oracles_pass":complete,"by_category":bycat,
        "results":results,"finished_at_utc":utc_iso(),
    }
    spath=batch_dir/"formal90_oracle_summary.json"; write_json(spath,summary)

    if complete:
        write_json(active_path,{
            "batch_id":batch_id,"catalog_sha256":catalog_hash,
            "status":"COMPLETE","completed_at_utc":utc_iso(),
            "summary_sha256":sha256_file(spath)
        })

    print("="*78)
    print("Formal90 oracle validation COMPLETE" if complete else "Formal90 oracle validation NOT YET 90/90")
    for cat,v in bycat.items():
        print(f"{cat:24s}: PASS={v['pass']}/{v['total']} FAIL={v['fail']} infra={v['infra']}")
    print(f"TOTAL                   : PASS={passed}/{len(results)} infra={infra}")
    print(f"summary                 : {spath}")
    print("="*78)
    return 0 if (complete or args.only) else 4

if __name__=="__main__":
    raise SystemExit(main())
