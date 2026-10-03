#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Formal Repair Feedback Sanitizer V0.1

Purpose
-------
Convert parser/static/runtime diagnostics into repair feedback that is useful
but does not directly expose hidden oracle values.

Design:
- whitelist what may be emitted;
- never forward raw validator/assertion dictionaries wholesale;
- use fixed public messages instead of validator-provided messages;
- remove expected/reference/allowed-answer fields;
- keep model-produced/observed evidence when useful;
- for runtime assertions, emit only failed assertions;
- do not alter scoring or validation; this module changes only what the model
  sees during the one-shot repair prompt.

This module is intended for Formal 90 and later experiments.
It must NOT be retroactively applied to already frozen Pilot scores.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Iterable, List, Optional

PROTOCOL_VERSION = "1.0"
SANITIZER_VERSION = "0.1"
SCHEMA_VERSION = "1"

# Never emit these fields even if a future validator adds them.
FORBIDDEN_KEY_FRAGMENTS = (
    "expected",
    "oracle",
    "gold",
    "correct",
    "reference",
    "answer",
    "allowed_sources",
    "accepted_values",
    "valid_values",
    "desired",
    "target_value",
    "required_value",
)

# Only these issue fields may be copied from parser/validator diagnostics.
# They represent the model's own artifact or observed parsing/validation state.
SAFE_ISSUE_FIELDS = {
    "key",
    "option",
    "raw",
    "token",
    "tokens",
    "lines",
    "ignored_lines",
    "positionals",
    "values",
    "sources",
    "observed",
    "observed_normalized",
    "observed_sources",
    "value",
    "options",
}

# Fixed messages prevent a future validator message from embedding the answer.
PUBLIC_MESSAGES = {
    # Parser / artifact extraction
    "MULTIPLE_FENCED_ARTIFACTS": "Multiple candidate Spark artifacts were found.",
    "EXTRA_NON_ARTIFACT_TEXT": "The response contains extra non-artifact text.",
    "NO_SPARK_ARTIFACT": "No supported Spark configuration artifact was found.",
    "MULTIPLE_SPARK_SUBMIT_COMMANDS": "More than one spark-submit command was found.",
    "SHELL_TOKENIZE_ERROR": "The spark-submit artifact could not be tokenized safely.",
    "UNSAFE_SHELL_SYNTAX": "Unsafe shell syntax is not accepted.",
    "UNSUPPORTED_ENV_ASSIGNMENT": "An unsupported environment assignment was used.",
    "NO_SPARK_SUBMIT_TOKEN": "A spark-submit token was not found.",
    "UNEXPECTED_PREFIX_TOKENS": "Unexpected tokens appear before spark-submit.",
    "FLAG_WITH_VALUE": "A flag was given a value although it does not take one.",
    "MISSING_OPTION_VALUE": "A spark-submit option is missing its value.",
    "INVALID_CONF_ASSIGNMENT": "--conf must use key=value form.",
    "EMPTY_CONF_KEY": "--conf contains an empty key.",
    "UNKNOWN_SUBMIT_OPTION": "An unknown spark-submit option was used.",
    "EMPTY_PROPERTY_VALUE": "A Spark property has an empty value.",
    "DUPLICATE_SAME_VALUE": "A Spark setting was specified more than once.",
    "CONFLICTING_VALUES": "A Spark setting was specified with conflicting values.",
    "COMMAND_REGION_AMBIGUOUS": "The spark-submit command region is ambiguous.",
    "UNPARSED_ARTIFACT_LINES": "Unsupported lines remain in the extracted artifact.",
    "NO_PARSED_SETTINGS": "No supported Spark settings were parsed.",

    # Static validator
    "UNAUTHORIZED_EXTRA_SUBMIT_OPTION": "The model added a spark-submit option not authorized by this task.",
    "UNAUTHORIZED_SETTING": "A Spark setting is outside this task's automatic-validation whitelist.",
    "INVALID_VALUE": "A Spark setting has an invalid value or format.",
    "MISSING_REQUIRED_SETTING": "A required Spark setting is missing.",
    "DISALLOWED_CONFIGURATION_SOURCE": "A Spark setting was supplied through a source not allowed by the task.",
    "UNSUPPORTED_VALIDATOR_KEY": "A Spark setting cannot be validated by the frozen validator profile.",
    "MODEL_POSITIONAL_ARGUMENT_NOT_ALLOWED": "Model-supplied positional application arguments are not executable in this benchmark.",
    "TASK_VALUE_MISMATCH": "A Spark setting does not satisfy the frozen task constraint.",
}

def _forbidden_name(name: str) -> bool:
    s = str(name).lower()
    return any(fragment in s for fragment in FORBIDDEN_KEY_FRAGMENTS)

def _sanitize_observed_value(value: Any) -> Any:
    """
    Preserve observed evidence while recursively removing fields that look
    oracle-derived. This is important for composite runtime observations such
    as {'peak': 4096, 'calculated': 5120}.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            kl = str(k).lower()
            if _forbidden_name(kl):
                continue
            # 'calculated' may be derived from task oracle values, so do not emit it.
            if kl in {"calculated", "threshold", "minimum_required", "maximum_allowed"}:
                continue
            out[k] = _sanitize_observed_value(v)
        return out
    if isinstance(value, list):
        return [_sanitize_observed_value(x) for x in value]
    return value

def _sanitize_options(value: Any) -> Any:
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        if isinstance(item, dict):
            slim = {}
            for k in ("option", "raw", "value"):
                if k in item and not _forbidden_name(k):
                    slim[k] = _sanitize_observed_value(item[k])
            if slim:
                out.append(slim)
        else:
            out.append(_sanitize_observed_value(item))
    return out

def sanitize_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    code = str(issue.get("code") or "UNKNOWN_DIAGNOSTIC")
    severity = str(issue.get("severity") or "ERROR")
    out: Dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": PUBLIC_MESSAGES.get(code, "The automated checker reported this issue."),
    }

    for field in SAFE_ISSUE_FIELDS:
        if field not in issue:
            continue
        if _forbidden_name(field):
            continue
        value = issue[field]
        if field == "options":
            value = _sanitize_options(value)
        else:
            value = _sanitize_observed_value(value)
        if value not in (None, [], {}, ""):
            out[field] = value
    return out

def sanitize_parser_feedback(parser_result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not parser_result:
        return None
    issues = [
        sanitize_issue(x)
        for x in (parser_result.get("issues") or [])
        if str(x.get("severity") or "ERROR").upper() == "ERROR"
    ]
    return {
        "parse_status": parser_result.get("parse_status"),
        "artifact_extraction_status": parser_result.get("artifact_extraction_status"),
        "issues": issues,
    }

def sanitize_static_feedback(static_validation: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not static_validation:
        return None
    issues = [
        sanitize_issue(x)
        for x in (static_validation.get("issues") or [])
        if str(x.get("severity") or "ERROR").upper() == "ERROR"
    ]
    return {
        "static_status": static_validation.get("static_status"),
        "issues": issues,
    }

def sanitize_runtime_assertion(assertion: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "name": assertion.get("name"),
        "passed": bool(assertion.get("passed")),
    }
    if "observed" in assertion:
        observed = assertion.get("observed")
        name = str(assertion.get("name") or "")

        # This assertion's composite 'calculated' field is derived partly from
        # task constraints. Keep only the actual YARN peak observation.
        if name == "yarn_memory_allocation" and isinstance(observed, dict):
            observed = {"peak": observed.get("peak")}
        else:
            observed = _sanitize_observed_value(observed)

        out["observed"] = observed
    return out

def sanitize_runtime_feedback(runtime_validation: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not runtime_validation:
        return None

    failed = []
    for item in (runtime_validation.get("assertions") or []):
        if item.get("passed") is False:
            failed.append(sanitize_runtime_assertion(item))

    return {
        "runtime_status": runtime_validation.get("runtime_status"),
        "application_id": runtime_validation.get("application_id"),
        "failed_assertions": failed,
    }

def build_repair_feedback(
    parser_result: Optional[Dict[str, Any]] = None,
    static_validation: Optional[Dict[str, Any]] = None,
    runtime_validation: Optional[Dict[str, Any]] = None,
    execution_stderr: Optional[str] = None,
    max_stderr_chars: int = 2000,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "feedback_protocol_version": PROTOCOL_VERSION,
        "sanitizer_version": SANITIZER_VERSION,
    }

    p = sanitize_parser_feedback(parser_result)
    s = sanitize_static_feedback(static_validation)
    r = sanitize_runtime_feedback(runtime_validation)
    if p is not None:
        out["parser"] = p
    if s is not None:
        out["static_validation"] = s
    if r is not None:
        out["runtime"] = r

    if execution_stderr:
        # Runtime stderr is direct system observation, not oracle data.
        # Keep only a bounded tail to control context size.
        out["execution_stderr_tail"] = execution_stderr[-max_stderr_chars:]

    assert_no_forbidden_keys(out)
    return out

def find_forbidden_keys(obj: Any, path: str = "") -> List[str]:
    found: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if _forbidden_name(str(k)) or str(k).lower() in {
                "calculated", "threshold", "minimum_required", "maximum_allowed"
            }:
                found.append(p)
            found.extend(find_forbidden_keys(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(find_forbidden_keys(v, f"{path}[{i}]"))
    return found

def assert_no_forbidden_keys(obj: Any) -> None:
    bad = find_forbidden_keys(obj)
    if bad:
        raise ValueError(f"Sanitized feedback still contains forbidden fields: {bad}")

def render_feedback_text(feedback: Dict[str, Any]) -> str:
    """
    Deterministic JSON rendering suitable for inclusion inside a repair prompt.
    """
    assert_no_forbidden_keys(feedback)
    return json.dumps(feedback, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    raise SystemExit(
        "This file is a library module. Run 49_test_repair_feedback_sanitizer_v01.py "
        "or 50_audit_formal_repair_feedback_protocol_v01.py."
    )
