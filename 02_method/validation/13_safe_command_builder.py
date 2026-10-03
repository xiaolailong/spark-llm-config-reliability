#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from typing import Any, Dict, List

BUILDER_VERSION = "0.1"
SCHEMA_VERSION = "1"

DEDICATED_KEY_TO_OPTION = {
    "spark.master": "--master",
    "spark.submit.deployMode": "--deploy-mode",
    "spark.driver.memory": "--driver-memory",
    "spark.executor.memory": "--executor-memory",
    "spark.driver.cores": "--driver-cores",
    "spark.executor.cores": "--executor-cores",
    "spark.executor.instances": "--num-executors",
    "spark.yarn.queue": "--queue",
    "spark.app.name": "--name",
}

class BuildRefused(RuntimeError):
    pass

def build_execution_plan(parser_result: Dict[str, Any], validation_result: Dict[str, Any]) -> Dict[str, Any]:
    if parser_result.get("parse_status") != "OK":
        raise BuildRefused("parse_status is not OK")
    if validation_result.get("static_status") != "PASS":
        raise BuildRefused("static_status is not PASS")
    if validation_result.get("safe_to_build") is not True:
        raise BuildRefused("safe_to_build is not true")

    canonical = parser_result.get("canonical") or {}
    if canonical.get("positionals"):
        raise BuildRefused("model positionals are never executable")
    if canonical.get("unknown_submit_options"):
        raise BuildRefused("unknown submit options are never executable")

    submit_args: List[str] = []
    spark_defaults: List[Dict[str, str]] = []

    settings = canonical.get("settings") or {}
    for key in sorted(settings):
        for entry in settings[key]:
            source = entry.get("source")
            value = str(entry.get("value"))
            if source == "dedicated_cli":
                option = entry.get("option") or DEDICATED_KEY_TO_OPTION.get(key)
                if option is None:
                    raise BuildRefused(f"No dedicated option mapping for {key}")
                submit_args.extend([option, value])
            elif source == "conf_cli":
                submit_args.extend(["--conf", f"{key}={value}"])
            elif source == "properties_text":
                spark_defaults.append({"key": key, "value": value})
            else:
                raise BuildRefused(f"Unsupported source: {source!r}")

    for item in canonical.get("extra_submit_options") or []:
        submit_args.append(item.get("option"))
        if item.get("value") is not None:
            submit_args.append(str(item.get("value")))

    return {
        "schema_version": SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "task_id": validation_result.get("task_id"),
        "model_submit_args": submit_args,
        "model_spark_defaults": spark_defaults,
        "model_environment": dict(canonical.get("environment") or {}),
        "fixed_application_resource_required": True,
        "shell_execution_allowed": False,
        "notes": [
            "Runner supplies fixed ConfigProbe class/jar/output path.",
            "model_spark_defaults is written only to per-run SPARK_CONF_DIR.",
            "Raw model response is never executed."
        ],
    }

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parser-result", required=True)
    ap.add_argument("--validation-result", required=True)
    ap.add_argument("--output-file")
    args = ap.parse_args()
    parser_result = json.loads(Path(args.parser_result).read_text(encoding="utf-8"))
    validation_result = json.loads(Path(args.validation_result).read_text(encoding="utf-8"))
    try:
        plan = build_execution_plan(parser_result, validation_result)
    except BuildRefused as exc:
        print(f"BUILD_REFUSED: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    if args.output_file:
        Path(args.output_file).write_text(rendered, encoding="utf-8")
        print(Path(args.output_file).resolve())
    else:
        print(rendered, end="")
    return 0

if __name__ == "__main__":
    sys.exit(main())
