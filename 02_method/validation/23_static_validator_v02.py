#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VALIDATOR_VERSION = "0.2"
SCHEMA_VERSION = "1"
MEMORY_RE = re.compile(r"^(\d+)\s*([kmgt])(?:i?b)?$", re.IGNORECASE)

def issue(code: str, message: str, severity: str = "ERROR", **extra: Any) -> Dict[str, Any]:
    out = {"code": code, "severity": severity, "message": message}
    out.update(extra)
    return out

def normalize_value(type_spec: Dict[str, Any], value: str) -> Tuple[bool, Any, Optional[str]]:
    kind = type_spec["type"]
    raw = str(value)
    if kind == "memory":
        m = MEMORY_RE.fullmatch(raw.strip())
        if not m:
            return False, None, "Memory must use a positive integer with k/m/g/t unit suffix."
        n = int(m.group(1))
        if n <= 0:
            return False, None, "Memory must be greater than zero."
        unit = m.group(2).lower()
        mib = {"k": n / 1024.0, "m": float(n), "g": float(n * 1024), "t": float(n * 1024 * 1024)}[unit]
        return True, {"mib": mib}, None
    if kind == "positive_int":
        try: n = int(raw.strip())
        except ValueError: return False, None, "Expected an integer."
        if n <= 0: return False, None, "Expected an integer greater than zero."
        return True, n, None
    if kind == "nonnegative_int":
        try: n = int(raw.strip())
        except ValueError: return False, None, "Expected an integer."
        if n < 0: return False, None, "Expected an integer greater than or equal to zero."
        return True, n, None
    if kind == "memory_mib":
        s = raw.strip().lower()
        # Spark memory-overhead properties are MiB unless a unit is specified.
        if re.fullmatch(r"\d+", s):
            n = int(s)
            if n <= 0:
                return False, None, "Memory must be greater than zero."
            return True, {"mib": float(n)}, None
        m = MEMORY_RE.fullmatch(s)
        if not m:
            return False, None, "Expected positive MiB integer or k/m/g/t size."
        n = int(m.group(1))
        if n <= 0:
            return False, None, "Memory must be greater than zero."
        unit = m.group(2).lower()
        mib = {
            "k": n / 1024.0,
            "m": float(n),
            "g": float(n * 1024),
            "t": float(n * 1024 * 1024),
        }[unit]
        return True, {"mib": mib}, None

    if kind == "boolean":
        s = raw.strip().lower()
        if s not in {"true", "false"}:
            return False, None, "Expected true or false."
        return True, s == "true", None
    if kind == "enum":
        s = raw.strip().lower()
        allowed = [str(x).lower() for x in type_spec["values"]]
        if s not in allowed:
            return False, None, f"Expected one of {allowed}."
        return True, s, None
    if kind == "nonempty_string":
        s = raw.strip()
        if not s:
            return False, None, "Expected a non-empty string."
        return True, s, None
    return False, None, f"Unsupported validator type: {kind}"

def merged_context(task: Dict[str, Any], normalized: Dict[str, Any]) -> Dict[str, Any]:
    ctx = dict(task.get("execution_context") or {})
    ctx.update(normalized)
    return ctx

def validate(parser_result: Dict[str, Any], task: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    supported = profile["supported_keys"]
    canonical = parser_result.get("canonical") or {}
    settings: Dict[str, List[Dict[str, Any]]] = canonical.get("settings") or {}

    if parser_result.get("parse_status") != "OK":
        issues.append(issue("PARSER_NOT_OK", "Static validation requires parse_status=OK.",
                            observed=parser_result.get("parse_status")))
    if parser_result.get("response_format_valid") is not True:
        issues.append(issue("RESPONSE_FORMAT_NONCOMPLIANT",
                            "Artifact is parseable, but the response violated the output-only format.",
                            severity="WARN"))

    unknown_options = canonical.get("unknown_submit_options") or []
    if unknown_options:
        issues.append(issue("UNKNOWN_SUBMIT_OPTION",
                            "Unknown spark-submit options are not eligible for automatic execution.",
                            options=unknown_options))

    allowed_extra = set(task.get("allowed_extra_submit_options") or [])
    for item in canonical.get("extra_submit_options") or []:
        if item.get("option") not in allowed_extra:
            issues.append(issue("UNAUTHORIZED_EXTRA_SUBMIT_OPTION",
                                "Model added a spark-submit option not authorized by this task.",
                                option=item.get("option"), value=item.get("value")))

    if canonical.get("positionals"):
        issues.append(issue("MODEL_POSITIONAL_ARGUMENT_NOT_ALLOWED",
                            "The fixed ConfigProbe application is supplied by the runner; model positionals are not executable.",
                            positionals=canonical.get("positionals")))

    allowed_env = set(task.get("allowed_environment") or [])
    for key, value in (canonical.get("environment") or {}).items():
        if key not in allowed_env:
            issues.append(issue("UNAUTHORIZED_ENVIRONMENT_ASSIGNMENT",
                                "Model-controlled environment assignment is not authorized.",
                                key=key, value=value))

    allowed_keys = set(task.get("allowed_keys") or [])
    required_exact = task.get("required_exact") or {}
    required_present = set(task.get("required_present") or [])
    forbidden = set(task.get("forbidden_keys") or [])
    locked = set(task.get("locked_keys") or [])
    allowed_sources = task.get("allowed_sources") or {}

    normalized: Dict[str, Any] = {}
    normalized_raw: Dict[str, Any] = {}

    for key, entries in settings.items():
        if key in locked:
            issues.append(issue("FIXTURE_LOCKED_KEY_EMITTED",
                                "Model attempted to set a fixture-controlled key.", key=key))
        if key in forbidden:
            issues.append(issue("FORBIDDEN_SETTING", "Task explicitly forbids this setting.", key=key))
        if key not in allowed_keys:
            issues.append(issue("UNAUTHORIZED_SETTING",
                                "Spark setting is outside this task's automatic-validation whitelist.", key=key))
        if key not in supported:
            issues.append(issue("UNSUPPORTED_VALIDATOR_KEY",
                                "Key is not supported by Static Validator V0.1.", key=key))
            continue

        vals = {str(x.get("value")) for x in entries}
        if len(vals) != 1:
            issues.append(issue("MULTIPLE_EFFECTIVE_VALUES",
                                "Static validator cannot choose among different values.",
                                key=key, values=sorted(vals)))
            continue

        entry = entries[-1]
        ok, norm, why = normalize_value(supported[key], entry.get("value"))
        if not ok:
            issues.append(issue("INVALID_VALUE", why or "Invalid value.",
                                key=key, observed=entry.get("value"), expected_type=supported[key]))
            continue
        normalized[key] = norm
        normalized_raw[key] = entry.get("value")

        allowed = allowed_sources.get(key)
        if allowed is not None:
            sources = {x.get("source") for x in entries}
            if any(s not in set(allowed) for s in sources):
                issues.append(issue("DISALLOWED_CONFIGURATION_SOURCE",
                                    "Value was supplied through a source not allowed by the task.",
                                    key=key, observed_sources=sorted(sources), allowed_sources=allowed))

    for key in sorted(required_present):
        if key not in settings:
            issues.append(issue("MISSING_REQUIRED_SETTING", "Required setting is missing.", key=key))

    for key, expected_raw in required_exact.items():
        if key not in settings:
            issues.append(issue("MISSING_REQUIRED_SETTING", "Required setting is missing.",
                                key=key, expected=expected_raw))
            continue
        if key not in supported or key not in normalized:
            continue
        ok, expected_norm, why = normalize_value(supported[key], str(expected_raw))
        if not ok:
            issues.append(issue("INVALID_TASK_ORACLE",
                                "Frozen task contains an invalid expected value.",
                                key=key, expected=expected_raw, note=why))
            continue
        if normalized[key] != expected_norm:
            issues.append(issue("TASK_VALUE_MISMATCH",
                                "Configuration does not satisfy the frozen task constraint.",
                                key=key, observed=normalized_raw.get(key), expected=expected_raw,
                                observed_normalized=normalized[key], expected_normalized=expected_norm))

    ctx = merged_context(task, normalized)
    master = ctx.get("spark.master")
    deploy = ctx.get("spark.submit.deployMode")

    if master is not None and master != "yarn":
        issues.append(issue("BENCHMARK_MASTER_MUST_BE_YARN",
                            "Benchmark profile is frozen to YARN.", observed=master, expected="yarn"))

    if "spark.yarn.submit.waitAppCompletion" in normalized:
        if master != "yarn" or deploy != "cluster":
            issues.append(issue("WAIT_COMPLETION_REQUIRES_YARN_CLUSTER",
                                "waitAppCompletion is validated only for YARN cluster mode.",
                                observed_master=master, observed_deploy_mode=deploy))

    if ctx.get("spark.dynamicAllocation.enabled") is True:
        shuffle_service = ctx.get("spark.shuffle.service.enabled")
        shuffle_tracking = ctx.get("spark.dynamicAllocation.shuffleTracking.enabled")
        if shuffle_tracking is None:
            shuffle_tracking = True  # Spark 3.5.9 documented default.
        if not (shuffle_service is True or shuffle_tracking is True):
            issues.append(issue("DYNAMIC_ALLOCATION_MISSING_SHUFFLE_PRESERVATION",
                                "Dynamic allocation lacks a recognized shuffle-preservation mechanism.",
                                shuffle_service=shuffle_service, shuffle_tracking=shuffle_tracking))

        min_e = ctx.get("spark.dynamicAllocation.minExecutors")
        init_e = ctx.get("spark.dynamicAllocation.initialExecutors")
        max_e = ctx.get("spark.dynamicAllocation.maxExecutors")
        if min_e is not None and max_e is not None and min_e > max_e:
            issues.append(issue("DYNAMIC_ALLOCATION_INVALID_BOUNDS",
                                "minExecutors must not exceed maxExecutors.",
                                minExecutors=min_e, maxExecutors=max_e))
        if init_e is not None and min_e is not None and init_e < min_e:
            issues.append(issue("DYNAMIC_ALLOCATION_INITIAL_BELOW_MIN",
                                "initialExecutors must not be below minExecutors.",
                                initialExecutors=init_e, minExecutors=min_e))
        if init_e is not None and max_e is not None and init_e > max_e:
            issues.append(issue("DYNAMIC_ALLOCATION_INITIAL_ABOVE_MAX",
                                "initialExecutors must not exceed maxExecutors.",
                                initialExecutors=init_e, maxExecutors=max_e))

    errors = [x for x in issues if x["severity"] == "ERROR"]
    return {
        "schema_version": SCHEMA_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "task_id": task.get("task_id"),
        "spark_version": profile.get("spark_version"),
        "response_format_valid": parser_result.get("response_format_valid"),
        "parse_status": parser_result.get("parse_status"),
        "static_status": "PASS" if not errors else "FAIL",
        "safe_to_build": not errors,
        "normalized_settings": normalized,
        "normalized_raw_values": normalized_raw,
        "issues": issues,
    }

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parser-result", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--profile", default="fixtures/validator_profile_v01.json")
    ap.add_argument("--output-file")
    args = ap.parse_args()
    root = Path(__file__).resolve().parent
    def load(path: str):
        p = Path(path)
        if not p.is_absolute(): p = root / p
        return json.loads(p.read_text(encoding="utf-8"))
    result = validate(load(args.parser_result), load(args.task), load(args.profile))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output_file:
        Path(args.output_file).write_text(rendered, encoding="utf-8")
        print(Path(args.output_file).resolve())
    else:
        print(rendered, end="")
    return 0 if result["static_status"] == "PASS" else 1

if __name__ == "__main__":
    sys.exit(main())
