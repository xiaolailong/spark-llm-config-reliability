#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Deterministic Spark configuration artifact parser V0.1.

Design contract:
- Extract what the model wrote.
- Never repair Spark semantics.
- Never execute raw model output.
- Preserve source/provenance for each setting.
- Surface duplicates, conflicts, unknown/extra options, and unsafe shell syntax.
- Keep response-format validity separate from parse validity.

This parser intentionally supports only the artifact forms needed by this study:
1) spark-submit command or bare spark-submit options;
2) Spark property lines;
3) a restricted SPARK_CONF_DIR=<path> prefix before spark-submit.

It does NOT parse arbitrary Java/Scala/Python SparkConf source code or JSON answers.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


PARSER_VERSION = "0.1.1"
SCHEMA_VERSION = "1"

# Dedicated spark-submit options that map to effective Spark configuration.
DEDICATED_VALUE_OPTIONS: Dict[str, str] = {
    "--master": "spark.master",
    "--deploy-mode": "spark.submit.deployMode",
    "--driver-memory": "spark.driver.memory",
    "--executor-memory": "spark.executor.memory",
    "--driver-cores": "spark.driver.cores",
    "--executor-cores": "spark.executor.cores",
    "--num-executors": "spark.executor.instances",
    "--queue": "spark.yarn.queue",
    "--name": "spark.app.name",
}

# Known spark-submit options that are syntactically valid but are not treated
# as benchmark configuration keys. They are preserved so static validation can
# reject harmful extras when a task did not authorize them.
KNOWN_VALUE_OPTIONS = {
    "--class",
    "--jars",
    "--packages",
    "--exclude-packages",
    "--repositories",
    "--py-files",
    "--files",
    "--archives",
    "--principal",
    "--keytab",
    "--proxy-user",
    "--properties-file",
    "--driver-java-options",
    "--driver-library-path",
    "--driver-class-path",
    "--conf",
}

KNOWN_FLAG_OPTIONS = {
    "--verbose",
    "--supervise",
    "--help",
    "--version",
}

SAFE_ENV_KEYS = {"SPARK_CONF_DIR"}

PROPERTY_RE = re.compile(r"^(spark\.[A-Za-z0-9_.-]+)\s*(?:=|\s)\s*(.*?)\s*$")
ENV_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

UNSAFE_SHELL_TOKENS = {";", "&&", "||", "|", ">", ">>", "<", "<<"}


def _issue(code: str, message: str, severity: str = "ERROR", **extra: Any) -> Dict[str, Any]:
    out = {"code": code, "severity": severity, "message": message}
    out.update(extra)
    return out


def _setting(
    key: str,
    value: str,
    source: str,
    raw: str,
    option: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "key": key,
        "value": value,
        "source": source,
        "option": option,
        "raw": raw,
    }


def _normalize_multiline_command(text: str) -> str:
    # Join explicit POSIX shell continuations while preserving ordinary lines.
    return re.sub(r"\\\s*\n\s*", " ", text)


def _strip_shell_prompt(line: str) -> str:
    s = line.strip()
    # Common copy/paste prompt markers only. Do not attempt arbitrary shell parsing.
    if s.startswith("$ "):
        return s[2:].lstrip()
    return s


def _looks_like_property_line(line: str) -> bool:
    s = line.strip()
    return bool(s and not s.startswith("#") and PROPERTY_RE.match(s))


def _looks_like_submit_options_line(line: str) -> bool:
    s = _strip_shell_prompt(line).strip()
    if not s or s.startswith("#"):
        return False
    try:
        tokens = shlex.split(s, posix=True)
    except ValueError:
        return False
    return bool(tokens and tokens[0].startswith("--"))


def _looks_like_command_text(text: str) -> bool:
    joined = _normalize_multiline_command(text)
    for line in joined.splitlines():
        s = _strip_shell_prompt(line)
        if "spark-submit" in s or _looks_like_submit_options_line(s):
            return True
    return False


def _artifact_score(text: str) -> int:
    score = 0
    if _looks_like_command_text(text):
        score += 10
    score += sum(1 for ln in text.splitlines() if _looks_like_property_line(ln))
    return score


def extract_artifact(raw_response: str) -> Dict[str, Any]:
    """
    Deterministically select one candidate artifact.

    Fenced candidate rule:
    - if one or more Markdown code fences contain Spark artifact material, use
      the unique highest-scoring candidate;
    - ties among different top candidates are AMBIGUOUS.

    Unfenced rule:
    - extract the spark-submit command region plus Spark property lines;
    - surrounding prose is ignored for parsing but makes response_format_valid=false.
    """
    raw = raw_response or ""
    fences = [m.group(1).strip() for m in FENCE_RE.finditer(raw)]
    candidate_fences = [(x, _artifact_score(x)) for x in fences if _artifact_score(x) > 0]

    if candidate_fences:
        max_score = max(score for _, score in candidate_fences)
        top = [text for text, score in candidate_fences if score == max_score]
        unique_top = []
        for x in top:
            if x not in unique_top:
                unique_top.append(x)
        if len(unique_top) > 1:
            return {
                "status": "AMBIGUOUS",
                "artifact_text": None,
                "artifact_kind_hint": None,
                "response_format_valid": False,
                "issues": [
                    _issue(
                        "MULTIPLE_FENCED_ARTIFACTS",
                        "Multiple distinct fenced Spark artifacts have equal extraction priority.",
                    )
                ],
            }

        chosen = unique_top[0]
        outside = FENCE_RE.sub("", raw).strip()
        format_valid = (outside == "" and len(candidate_fences) == 1)
        return {
            "status": "OK",
            "artifact_text": chosen,
            "artifact_kind_hint": "fenced",
            "response_format_valid": format_valid,
            "issues": [] if format_valid else [
                _issue(
                    "EXTRA_NON_ARTIFACT_TEXT",
                    "Artifact is extractable, but the response also contains extra text or multiple code fences.",
                    severity="WARN",
                )
            ],
        }

    # No useful fenced artifact. Build an unfenced candidate.
    joined = _normalize_multiline_command(raw)
    lines = joined.splitlines()

    command_lines: List[str] = []
    option_lines: List[str] = []
    property_lines: List[str] = []
    prose_lines: List[str] = []

    for line in lines:
        s = _strip_shell_prompt(line)
        if not s:
            continue
        if "spark-submit" in s:
            command_lines.append(s)
        elif _looks_like_submit_options_line(s):
            option_lines.append(s)
        elif _looks_like_property_line(s):
            property_lines.append(s)
        elif s.startswith("#"):
            # Comments are tolerated inside property artifacts.
            continue
        else:
            prose_lines.append(s)

    if command_lines and option_lines:
        return {
            "status": "AMBIGUOUS",
            "artifact_text": "\n".join(command_lines + option_lines + property_lines),
            "artifact_kind_hint": "unfenced",
            "response_format_valid": False,
            "issues": [
                _issue(
                    "MIXED_FULL_COMMAND_AND_BARE_OPTIONS",
                    "A full spark-submit command and separate bare submission-option lines were both found.",
                )
            ],
        }

    # One full command, OR one/more bare option lines treated as one option region,
    # plus optional property lines, is allowed as one extracted artifact.
    artifact_parts: List[str] = []
    if command_lines:
        artifact_parts.extend(command_lines)
    elif option_lines:
        artifact_parts.append(" ".join(option_lines))
    if property_lines:
        artifact_parts.extend(property_lines)

    if not artifact_parts:
        return {
            "status": "PARSE_FAIL",
            "artifact_text": None,
            "artifact_kind_hint": None,
            "response_format_valid": False,
            "issues": [
                _issue("NO_SPARK_ARTIFACT", "No supported Spark configuration artifact was found.")
            ],
        }

    if len(command_lines) > 1:
        return {
            "status": "AMBIGUOUS",
            "artifact_text": "\n".join(artifact_parts),
            "artifact_kind_hint": "unfenced",
            "response_format_valid": False,
            "issues": [
                _issue(
                    "MULTIPLE_SPARK_SUBMIT_COMMANDS",
                    "More than one spark-submit command was found in one response.",
                )
            ],
        }

    artifact = "\n".join(artifact_parts)
    format_valid = len(prose_lines) == 0
    return {
        "status": "OK",
        "artifact_text": artifact,
        "artifact_kind_hint": "unfenced",
        "response_format_valid": format_valid,
        "issues": [] if format_valid else [
            _issue(
                "EXTRA_NON_ARTIFACT_TEXT",
                "Artifact is extractable, but explanatory/non-artifact text is also present.",
                severity="WARN",
                ignored_lines=prose_lines,
            )
        ],
    }


def _split_option_token(token: str) -> Tuple[str, Optional[str]]:
    if token.startswith("--") and "=" in token:
        name, value = token.split("=", 1)
        return name, value
    return token, None


def _contains_unsafe_shell(text: str, tokens: List[str]) -> Optional[str]:
    # Explicit control/redirection tokens.
    for token in tokens:
        if token in UNSAFE_SHELL_TOKENS:
            return token
        if "$(" in token or "`" in token:
            return token
    # Semicolon attached to an argument is also unsafe in a model-produced shell command.
    if re.search(r"(^|\s);(\s|$)", text):
        return ";"
    return None


def parse_spark_submit(command_text: str) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    settings: List[Dict[str, Any]] = []
    extra_options: List[Dict[str, Any]] = []
    unknown_options: List[Dict[str, Any]] = []
    positionals: List[str] = []
    environment: Dict[str, str] = {}

    normalized = _normalize_multiline_command(command_text).strip()

    try:
        tokens = shlex.split(normalized, posix=True)
    except ValueError as exc:
        return {
            "status": "PARSE_FAIL",
            "settings": [],
            "extra_options": [],
            "unknown_options": [],
            "positionals": [],
            "environment": {},
            "issues": [_issue("SHELL_TOKENIZE_ERROR", str(exc))],
        }

    unsafe = _contains_unsafe_shell(normalized, tokens)
    if unsafe is not None:
        return {
            "status": "PARSE_FAIL",
            "settings": [],
            "extra_options": [],
            "unknown_options": [],
            "positionals": [],
            "environment": {},
            "issues": [
                _issue(
                    "UNSAFE_SHELL_SYNTAX",
                    "Shell control/substitution syntax is not accepted by the deterministic parser.",
                    token=unsafe,
                )
            ],
        }

    # Permit only restricted leading environment assignments.
    idx = 0
    while idx < len(tokens):
        m = ENV_ASSIGN_RE.match(tokens[idx])
        if not m:
            break
        key, value = m.group(1), m.group(2)
        if key not in SAFE_ENV_KEYS:
            issues.append(
                _issue(
                    "UNSUPPORTED_ENV_ASSIGNMENT",
                    f"Environment assignment {key}=... is not supported.",
                    key=key,
                )
            )
        else:
            environment[key] = value
        idx += 1

    # Find spark-submit as a token, not a substring.
    submit_idx = None
    for i in range(idx, len(tokens)):
        base = tokens[i].rsplit("/", 1)[-1]
        if base == "spark-submit":
            submit_idx = i
            break

    if submit_idx is None:
        return {
            "status": "PARSE_FAIL",
            "settings": [],
            "extra_options": [],
            "unknown_options": [],
            "positionals": [],
            "environment": environment,
            "issues": issues + [_issue("NO_SPARK_SUBMIT_TOKEN", "spark-submit token not found.")],
        }

    # Tokens before spark-submit (after allowed env assignments) are not accepted.
    if submit_idx != idx:
        issues.append(
            _issue(
                "UNEXPECTED_PREFIX_TOKENS",
                "Unexpected tokens appear before spark-submit.",
                tokens=tokens[idx:submit_idx],
            )
        )

    i = submit_idx + 1
    while i < len(tokens):
        token = tokens[i]

        if not token.startswith("--"):
            positionals.append(token)
            i += 1
            continue

        name, inline_value = _split_option_token(token)

        if name in KNOWN_FLAG_OPTIONS:
            if inline_value is not None:
                issues.append(
                    _issue(
                        "FLAG_WITH_VALUE",
                        f"{name} does not take a value.",
                        option=name,
                        raw=token,
                    )
                )
            extra_options.append({"option": name, "value": None, "raw": token, "known": True})
            i += 1
            continue

        if name in DEDICATED_VALUE_OPTIONS or name in KNOWN_VALUE_OPTIONS:
            if inline_value is not None:
                value = inline_value
                consumed = 1
            else:
                if i + 1 >= len(tokens) or tokens[i + 1].startswith("--"):
                    issues.append(
                        _issue(
                            "MISSING_OPTION_VALUE",
                            f"{name} requires a value.",
                            option=name,
                            raw=token,
                        )
                    )
                    i += 1
                    continue
                value = tokens[i + 1]
                consumed = 2

            raw_pair = token if inline_value is not None else f"{token} {tokens[i + 1]}"

            if name == "--conf":
                if "=" not in value:
                    issues.append(
                        _issue(
                            "INVALID_CONF_ASSIGNMENT",
                            "--conf must contain key=value.",
                            raw=raw_pair,
                        )
                    )
                else:
                    key, conf_value = value.split("=", 1)
                    if not key:
                        issues.append(
                            _issue("EMPTY_CONF_KEY", "--conf contains an empty key.", raw=raw_pair)
                        )
                    else:
                        settings.append(
                            _setting(
                                key=key,
                                value=conf_value,
                                source="conf_cli",
                                raw=raw_pair,
                                option="--conf",
                            )
                        )
            elif name in DEDICATED_VALUE_OPTIONS:
                settings.append(
                    _setting(
                        key=DEDICATED_VALUE_OPTIONS[name],
                        value=value,
                        source="dedicated_cli",
                        raw=raw_pair,
                        option=name,
                    )
                )
            else:
                extra_options.append(
                    {"option": name, "value": value, "raw": raw_pair, "known": True}
                )

            i += consumed
            continue

        # Unknown --option. Preserve it but do not guess its arity.
        unknown_options.append({"option": name, "raw": token})
        issues.append(
            _issue(
                "UNKNOWN_SUBMIT_OPTION",
                f"Unknown spark-submit option preserved without semantic interpretation: {name}",
                severity="WARN",
                option=name,
            )
        )
        i += 1

    return {
        "status": "OK",
        "settings": settings,
        "extra_options": extra_options,
        "unknown_options": unknown_options,
        "positionals": positionals,
        "environment": environment,
        "issues": issues,
    }


def parse_property_lines(text: str) -> Dict[str, Any]:
    settings: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    ignored: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "spark-submit" in line:
            continue

        m = PROPERTY_RE.match(line)
        if not m:
            ignored.append(line)
            continue

        key, value = m.group(1), m.group(2)
        if value == "":
            issues.append(
                _issue(
                    "EMPTY_PROPERTY_VALUE",
                    f"Spark property {key} has an empty value.",
                    key=key,
                    raw=line,
                )
            )
        settings.append(
            _setting(
                key=key,
                value=value,
                source="properties_text",
                raw=line,
                option=None,
            )
        )

    return {
        "settings": settings,
        "issues": issues,
        "ignored_lines": ignored,
    }


def analyze_setting_conflicts(settings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for item in settings:
        by_key.setdefault(item["key"], []).append(item)

    issues: List[Dict[str, Any]] = []

    for key, entries in by_key.items():
        values = [x["value"] for x in entries]
        unique_values = []
        for v in values:
            if v not in unique_values:
                unique_values.append(v)

        if len(entries) > 1 and len(unique_values) == 1:
            issues.append(
                _issue(
                    "DUPLICATE_SAME_VALUE",
                    f"{key} is specified more than once with the same value.",
                    severity="WARN",
                    key=key,
                    values=values,
                    sources=[x["source"] for x in entries],
                )
            )
        elif len(unique_values) > 1:
            issues.append(
                _issue(
                    "CONFLICTING_VALUES",
                    f"{key} is specified with conflicting values.",
                    key=key,
                    values=unique_values,
                    sources=[x["source"] for x in entries],
                )
            )

    return issues


def _canonical_groups(settings: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for item in settings:
        out.setdefault(item["key"], []).append(
            {
                "value": item["value"],
                "source": item["source"],
                "option": item.get("option"),
                "raw": item["raw"],
            }
        )
    return dict(sorted(out.items()))


def parse_response(raw_response: str) -> Dict[str, Any]:
    extraction = extract_artifact(raw_response)

    base = {
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "response_format_valid": extraction["response_format_valid"],
        "artifact_extraction_status": extraction["status"],
        "artifact_text": extraction.get("artifact_text"),
        "parse_status": extraction["status"] if extraction["status"] != "OK" else None,
        "canonical": {
            "settings": {},
            "environment": {},
            "extra_submit_options": [],
            "unknown_submit_options": [],
            "positionals": [],
        },
        "issues": list(extraction.get("issues") or []),
    }

    if extraction["status"] != "OK":
        return base

    artifact = extraction["artifact_text"] or ""
    all_settings: List[Dict[str, Any]] = []
    issues = list(base["issues"])

    command_result = None
    if _looks_like_command_text(artifact):
        # Parse either one full spark-submit command or a bare submission-options
        # region. Property-only lines are handled separately below.
        command_lines = []
        option_lines = []
        for x in _normalize_multiline_command(artifact).splitlines():
            s = _strip_shell_prompt(x)
            if "spark-submit" in s:
                command_lines.append(s)
            elif _looks_like_submit_options_line(s):
                option_lines.append(s)

        if command_lines and option_lines:
            base["parse_status"] = "AMBIGUOUS"
            base["issues"] = issues + [
                _issue(
                    "MIXED_FULL_COMMAND_AND_BARE_OPTIONS",
                    "A full spark-submit command and bare submission options were both found.",
                )
            ]
            return base

        if len(command_lines) > 1:
            base["parse_status"] = "AMBIGUOUS"
            base["issues"] = issues + [
                _issue(
                    "COMMAND_REGION_AMBIGUOUS",
                    "Expected exactly one spark-submit command after extraction.",
                )
            ]
            return base

        if command_lines:
            command_text = command_lines[0]
        elif option_lines:
            command_text = "spark-submit " + " ".join(option_lines)
        else:
            command_text = None

        if command_text is None:
            base["parse_status"] = "PARSE_FAIL"
            base["issues"] = issues + [
                _issue(
                    "NO_COMMAND_REGION",
                    "No supported spark-submit command/options region was found.",
                )
            ]
            return base

        command_result = parse_spark_submit(command_text)
        issues.extend(command_result["issues"])
        if command_result["status"] != "OK":
            base["parse_status"] = command_result["status"]
            base["canonical"]["environment"] = command_result.get("environment") or {}
            base["issues"] = issues
            return base

        all_settings.extend(command_result["settings"])
        base["canonical"]["environment"] = command_result["environment"]
        base["canonical"]["extra_submit_options"] = command_result["extra_options"]
        base["canonical"]["unknown_submit_options"] = command_result["unknown_options"]
        base["canonical"]["positionals"] = command_result["positionals"]

    prop_result = parse_property_lines(artifact)
    all_settings.extend(prop_result["settings"])
    issues.extend(prop_result["issues"])

    # Unsupported non-property lines in a property-only artifact are a parse error.
    if command_result is None and prop_result["ignored_lines"]:
        issues.append(
            _issue(
                "UNPARSED_ARTIFACT_LINES",
                "Unsupported lines remain in the extracted property artifact.",
                lines=prop_result["ignored_lines"],
            )
        )

    if not all_settings and command_result is None:
        base["parse_status"] = "PARSE_FAIL"
        base["issues"] = issues + [
            _issue("NO_PARSED_SETTINGS", "No supported Spark settings were parsed.")
        ]
        return base

    conflict_issues = analyze_setting_conflicts(all_settings)
    issues.extend(conflict_issues)

    base["canonical"]["settings"] = _canonical_groups(all_settings)
    base["issues"] = issues

    error_codes = {
        x["code"] for x in issues if x.get("severity") == "ERROR"
    }
    if "CONFLICTING_VALUES" in error_codes:
        base["parse_status"] = "AMBIGUOUS"
    elif error_codes:
        # Extraction succeeded but the artifact itself contains unsupported/broken syntax.
        base["parse_status"] = "PARSE_FAIL"
    else:
        base["parse_status"] = "OK"

    return base


def main() -> int:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--input-file")
    group.add_argument("--text")
    ap.add_argument("--output-file")
    args = ap.parse_args()

    if args.input_file:
        raw = Path(args.input_file).read_text(encoding="utf-8")
    else:
        raw = args.text

    result = parse_response(raw)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"

    if args.output_file:
        Path(args.output_file).write_text(rendered, encoding="utf-8")
        print(Path(args.output_file).resolve())
    else:
        print(rendered, end="")

    return 0 if result["parse_status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
