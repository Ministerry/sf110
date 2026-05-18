#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import re
from collections import defaultdict
from typing import Any, Dict, List, Sequence

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MaxAbsScaler


DEFAULT_INPUT = "/home/ubuntu/myren/SF110/soot_mutant_static_one.json"
DEFAULT_OUTPUT_DIR = "/home/ubuntu/myren/SF110/artifacts/static_reward_baseline"


def clamp01(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def safe_div(numer: float, denom: float) -> float:
    if not denom:
        return 0.0
    return float(numer) / float(denom)


def load_json(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_text(text: str) -> str:
    text = text or ""
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def extract_assertion_skeleton(assertion: str) -> str:
    skeleton = re.sub(r"\s+", "", assertion or "")
    skeleton = re.sub(
        r"\b(?:Double|Float)\.(?:NaN|POSITIVE_INFINITY|NEGATIVE_INFINITY)\b",
        "CONST_SPECIAL_FLOAT",
        skeleton,
        flags=re.IGNORECASE,
    )
    skeleton = re.sub(r'"(?:\\.|[^"\\])*"', "CONST_STRING", skeleton)
    skeleton = re.sub(r"'(?:\\.|[^'\\])*'", "CONST_CHAR", skeleton)
    skeleton = re.sub(r"\b[+-]?\d+\.\d+(?:[dDfF])?\b", "CONST_FLOAT", skeleton)
    skeleton = re.sub(r"\b[+-]?\d+(?:[lL])?\b", "CONST_INT", skeleton)

    keep = {
        "asserttrue", "assertfalse", "assertequals", "assertnotequals", "assertnull",
        "assertnotnull", "assertsame", "assertnotsame", "assertarrayequals",
        "assertthrows", "assertthat", "fail", "true", "false", "null",
        "CONST_SPECIAL_FLOAT", "CONST_STRING", "CONST_CHAR", "CONST_FLOAT", "CONST_INT",
    }

    def replace_identifier(match):
        token = match.group(0)
        if token.lower() in keep:
            return token.lower()
        return "var"

    skeleton = re.sub(r'(?<!\.)\b[A-Za-z_][A-Za-z0-9_]*\b(?!\s*\()', replace_identifier, skeleton)
    return skeleton.lower()


def get_focal_var(soot: Dict[str, Any]) -> Dict[str, Any]:
    for v in soot.get("variables", []) or []:
        if v.get("role") == "Focal Object (State Owner)":
            return v
    return {}


def extract_soot_features(soot: Dict[str, Any]) -> Dict[str, float]:
    focal = get_focal_var(soot)
    cfi = focal.get("control_flow_influence", {}) or {}
    dsa = focal.get("data_flow_state_access", {}) or {}
    return_sites = ((soot.get("return_flow") or {}).get("sites") or [])

    ret_const_sites = sum(1 for s in return_sites if s.get("returns_constant"))
    ret_null_sites = sum(
        1 for s in return_sites
        if any(u.get("type") == "null_type" for u in (s.get("uses") or []))
    )

    return {
        "branches": float(len(soot.get("branch_sites", []) or [])),
        "paths": float(((soot.get("global_metrics") or {}).get("total_estimated_paths", 0))),
        "returns": float(((soot.get("return_flow") or {}).get("return_site_count", 0))),
        "pdg_nodes": float(((soot.get("global_metrics") or {}).get("pdg_node_count", 0))),
        "units": float(((soot.get("raw_summary") or {}).get("unit_count", 0))),
        "invoke_units": float(((soot.get("raw_summary") or {}).get("invoke_unit_count", 0))),
        "throw_units": float(((soot.get("raw_summary") or {}).get("throw_unit_count", 0))),
        "focal_branch_state": float(cfi.get("branch_decisions_dependent_on_state", 0)),
        "focal_exception_paths": float(cfi.get("exception_paths", 0)),
        "focal_branch_cond": float(cfi.get("branch_condition_usage", 0)),
        "focal_pdg_dependants": float(cfi.get("pdg_dependants", 0)),
        "focal_path_constraints": float(len(cfi.get("path_constraints", []) or [])),
        "focal_fields_read": float(dsa.get("fields_read", 0)),
        "focal_fields_modified": float(dsa.get("fields_modified", 0)),
        "focal_static_reads": float(dsa.get("static_fields_read", 0)),
        "ret_const_sites": float(ret_const_sites),
        "ret_null_sites": float(ret_null_sites),
        "ret_constant_flag": 1.0 if ret_const_sites > 0 else 0.0,
        "ret_null_flag": 1.0 if ret_null_sites > 0 else 0.0,
    }


def assertion_kind(assertion: str) -> str:
    a = (assertion or "").lower()
    if "assertnotnull" in a:
        return "notnull"
    if "assertnull" in a:
        return "null"
    if "assertthrows" in a:
        return "throws"
    if "asserttrue" in a or "assertfalse" in a:
        return "boolean"
    if "assertequals" in a or "assertnotequals" in a:
        return "equals"
    if "assertsame" in a or "assertnotsame" in a:
        return "same"
    if "assertarrayequals" in a:
        return "array"
    if "assertthat" in a:
        return "that"
    if "fail(" in a:
        return "fail"
    return "other"


def assertion_signals(assertion: str) -> List[str]:
    a = (assertion or "").lower()
    tokens: List[str] = [f"assert_kind={assertion_kind(assertion)}"]

    if re.search(r"\btrue\b|\bfalse\b", a):
        tokens.append("assert_has_boolean_literal")
    if re.search(r"\bnull\b", a):
        tokens.append("assert_has_null")
    if re.search(r'"(?:\\.|[^"\\])*"', assertion or ""):
        tokens.append("assert_has_string_literal")
    if re.search(r"\b[+-]?\d+(?:\.\d+)?(?:[fl])?\b", a):
        tokens.append("assert_has_numeric_literal")
    if re.search(r"\.(get|is|has|contains|size|length|count|toString|matches)\w*\s*\(", a):
        tokens.append("assert_observes_accessor")
    if re.search(r"\.(equals|compareto|contains)\s*\(", a):
        tokens.append("assert_observes_comparison")
    if re.search(r"\bexception\b|\bthrow\b", a):
        tokens.append("assert_exception_style")
    if re.search(r"\bsize\s*\(|\blength\s*\(|\bcount\s*\(", a):
        tokens.append("assert_collection_metric")

    length = len(assertion or "")
    if length < 40:
        tokens.append("assert_len_short")
    elif length < 100:
        tokens.append("assert_len_medium")
    else:
        tokens.append("assert_len_long")

    method_calls = len(re.findall(r"\.[A-Za-z_][A-Za-z0-9_]*\s*\(", assertion or ""))
    if method_calls == 0:
        tokens.append("assert_calls_0")
    elif method_calls <= 2:
        tokens.append("assert_calls_1_2")
    else:
        tokens.append("assert_calls_3plus")
    return tokens


def text_buckets(text: str, prefix: str) -> List[str]:
    low = (text or "").lower()
    tokens: List[str] = []
    for name, pattern in [
        ("if", r"\bif\b"),
        ("else", r"\belse\b"),
        ("for", r"\bfor\b"),
        ("while", r"\bwhile\b"),
        ("return", r"\breturn\b"),
        ("throw", r"\bthrow\b"),
        ("new", r"\bnew\b"),
        ("null", r"\bnull\b"),
        ("assert", r"\bassert(?:true|false|equals|notnull|null|same|notsame|arrayequals|throws|that)\b"),
    ]:
        tokens.append(f"{prefix}_{name}_count={len(re.findall(pattern, low))}")

    calls = len(re.findall(r"\.[A-Za-z_][A-Za-z0-9_]*\s*\(", low))
    tokens.append(f"{prefix}_method_calls={calls}")

    nums = len(re.findall(r"\b[+-]?\d+(?:\.\d+)?(?:[fl])?\b", low))
    tokens.append(f"{prefix}_numeric_literals={nums}")
    strs = len(re.findall(r'"(?:\\.|[^"\\])*"', text or ""))
    tokens.append(f"{prefix}_string_literals={strs}")
    return tokens


def extract_structured_assertion_features(assertion: str) -> Dict[str, float]:
    a = assertion or ""
    low = a.lower()
    numeric_literals = re.findall(r"\b[+-]?\d+(?:\.\d+)?(?:[fl])?\b", low)
    float_literals = re.findall(r"\b[+-]?\d+\.\d+(?:[fl])?\b", low)
    int_literals = re.findall(r"\b[+-]?\d+(?:[l])?\b", low)

    features = {
        "assert_len": float(len(a)),
        "assert_method_calls": float(len(re.findall(r"\.[A-Za-z_][A-Za-z0-9_]*\s*\(", a))),
        "assert_numeric_literals": float(len(numeric_literals)),
        "assert_float_literals": float(len(float_literals)),
        "assert_int_literals": float(len(int_literals)),
        "assert_string_literals": float(len(re.findall(r'"(?:\\.|[^"\\])*"', a))),
        "assert_null_mentions": float(len(re.findall(r"\bnull\b", low))),
        "assert_boolean_mentions": float(len(re.findall(r"\btrue\b|\bfalse\b", low))),
        "assert_getter_calls": float(len(re.findall(r"\.(get|is|has|contains|size|length|count|toString|matches)\w*\s*\(", low))),
        "assert_comparison_calls": float(len(re.findall(r"\.(equals|compareto|contains)\s*\(", low))),
        "assert_arith_ops": float(len(re.findall(r"[+\-*/%]", a))),
        "assert_cmp_ops": float(len(re.findall(r"(?:<=|>=|==|!=|<|>)", a))),
        "assert_plus_one": 1.0 if re.search(r"\+\s*1\b|\b1\s*\+", a) else 0.0,
        "assert_minus_one": 1.0 if re.search(r"-\s*1\b|\b1\s*-", a) else 0.0,
        "assert_zero": 1.0 if re.search(r"\b0(?:[lLfF])?\b", a) else 0.0,
        "assert_one": 1.0 if re.search(r"\b1(?:[lLfF])?\b", a) else 0.0,
        "assert_notnull": 1.0 if "assertnotnull" in low else 0.0,
        "assert_null": 1.0 if "assertnull" in low else 0.0,
        "assert_boolean": 1.0 if "asserttrue" in low or "assertfalse" in low else 0.0,
        "assert_equals": 1.0 if "assertequals" in low else 0.0,
        "assert_notequals": 1.0 if "assertnotequals" in low else 0.0,
        "assert_throws": 1.0 if "assertthrows" in low else 0.0,
        "assert_contains_api": 1.0 if re.search(r"\.contains\w*\s*\(", low) else 0.0,
        "assert_index_api": 1.0 if re.search(r"\.indexof\s*\(|\.lastindexof\s*\(|\.substring\s*\(", low) else 0.0,
        "assert_string_api": 1.0 if re.search(r"\.(startswith|endswith|matches|replace|trim)\w*\s*\(", low) else 0.0,
        "assert_collection_api": 1.0 if re.search(r"\.(size|length|count|isempty)\w*\s*\(|\.length\b", low) else 0.0,
        "assert_len_short": 1.0 if len(a) < 40 else 0.0,
        "assert_len_medium": 1.0 if 40 <= len(a) < 100 else 0.0,
        "assert_len_long": 1.0 if len(a) >= 100 else 0.0,
    }
    return features


def extract_method_names(text: str) -> List[str]:
    text = text or ""
    names = re.findall(r"(?:\.|\b)([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
    blocked = {
        "if", "for", "while", "switch", "catch", "asserttrue", "assertfalse",
        "assertequals", "assertnotequals", "assertnotnull", "assertnull",
        "assertsame", "assertnotsame", "assertarrayequals", "assertthrows",
        "assertthat", "fail",
    }
    return [name for name in names if name.lower() not in blocked]


def extract_receivers(text: str) -> List[str]:
    return re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*[A-Za-z_][A-Za-z0-9_]*\s*\(", text or "")


def extract_declared_vars(text: str) -> List[str]:
    text = text or ""
    vars_found = re.findall(
        r"\b(?:[A-Z][A-Za-z0-9_<>\[\].?,\s]*|boolean|byte|short|int|long|float|double|char|String)\s+"
        r"([a-zA-Z_][A-Za-z0-9_]*)\s*(?:=|;|,)",
        text,
    )
    return vars_found


def extract_return_var(prefix: str, method_name: str) -> str:
    if not method_name:
        return ""
    pattern = (
        r"\b(?:[A-Z][A-Za-z0-9_<>\[\].?,\s]*|boolean|byte|short|int|long|float|double|char|String)\s+"
        r"([a-zA-Z_][A-Za-z0-9_]*)\s*=\s*[^;]*\b"
        + re.escape(method_name)
        + r"\s*\("
    )
    match = re.search(pattern, prefix or "")
    return match.group(1) if match else ""


def extract_focal_receiver(prefix: str, method_name: str) -> str:
    if not method_name:
        return ""
    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*" + re.escape(method_name) + r"\s*\(", prefix or "")
    return match.group(1) if match else ""


def extract_context_features(entry: Dict[str, Any], assertion: str) -> Dict[str, float]:
    prefix = entry.get("prefix", "") or ""
    focal_prefix = entry.get("focal_prefix", "") or ""
    focal_method = entry.get("focal_method", "") or ""
    method_name = entry.get("extracted_method_name", "") or ""
    combined_context = "\n".join([prefix, focal_prefix, focal_method])

    assertion_methods = set(extract_method_names(assertion))
    context_methods = set(extract_method_names(combined_context))
    focal_methods = set(extract_method_names(focal_method))
    assertion_receivers = set(extract_receivers(assertion))
    prefix_receivers = set(extract_receivers(prefix))
    declared_vars = set(extract_declared_vars(prefix))
    return_var = extract_return_var(prefix, method_name)
    focal_receiver = extract_focal_receiver(prefix, method_name)

    overlap_context = assertion_methods & context_methods
    overlap_focal = assertion_methods & focal_methods
    receiver_overlap = assertion_receivers & (prefix_receivers | declared_vars)

    a = assertion or ""
    low_a = a.lower()
    low_focal = focal_method.lower()
    low_prefix = prefix.lower()

    features = {
        "ctx_assert_method_count": float(len(assertion_methods)),
        "ctx_context_method_count": float(len(context_methods)),
        "ctx_api_overlap_count": float(len(overlap_context)),
        "ctx_focal_api_overlap_count": float(len(overlap_focal)),
        "ctx_api_overlap_ratio": safe_div(len(overlap_context), len(assertion_methods)),
        "ctx_focal_api_overlap_ratio": safe_div(len(overlap_focal), len(assertion_methods)),
        "ctx_receiver_count": float(len(assertion_receivers)),
        "ctx_receiver_overlap_count": float(len(receiver_overlap)),
        "ctx_receiver_overlap_ratio": safe_div(len(receiver_overlap), len(assertion_receivers)),
        "ctx_declared_var_count": float(len(declared_vars)),
        "ctx_assert_uses_return_var": 1.0 if return_var and re.search(r"\b" + re.escape(return_var) + r"\b", a) else 0.0,
        "ctx_assert_uses_focal_receiver": 1.0 if focal_receiver and re.search(r"\b" + re.escape(focal_receiver) + r"\b", a) else 0.0,
        "ctx_assert_uses_any_declared_var": 1.0 if any(re.search(r"\b" + re.escape(v) + r"\b", a) for v in declared_vars) else 0.0,
        "ctx_assert_calls_focal_method": 1.0 if method_name and re.search(r"\b" + re.escape(method_name) + r"\s*\(", a) else 0.0,
        "ctx_assert_has_param_call": 1.0 if re.search(r"\.[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*,[^)]*\)", a) else 0.0,
        "ctx_focal_has_arith": 1.0 if re.search(r"[+\-*/%]", focal_method) else 0.0,
        "ctx_focal_has_cmp": 1.0 if re.search(r"(?:<=|>=|==|!=|<|>)", focal_method) else 0.0,
        "ctx_focal_has_index": 1.0 if re.search(r"\[[^\]]+\]|\bindex\b|indexof|lastindexof", low_focal) else 0.0,
        "ctx_focal_has_string_api": 1.0 if re.search(r"startswith|endswith|substring|indexof|lastindexof|matches|replace|trim", low_focal) else 0.0,
        "ctx_prefix_has_same_assert_api": 1.0 if any(m.lower() in low_prefix for m in assertion_methods) else 0.0,
        "ctx_assert_null_guard": 1.0 if re.search(r"(?:==|!=)\s*null|null\s*(?:==|!=)", low_a) else 0.0,
        "ctx_assert_instanceof": 1.0 if "instanceof" in low_a else 0.0,
    }
    return features


def context_tokens(entry: Dict[str, Any], assertion: str) -> List[str]:
    prefix = entry.get("prefix", "") or ""
    focal_method = entry.get("focal_method", "") or ""
    method_name = entry.get("extracted_method_name", "") or ""
    tokens: List[str] = []
    for name in sorted(set(extract_method_names(assertion))):
        tokens.append(f"assert_call={name.lower()}")
    for name in sorted(set(extract_method_names(focal_method))):
        tokens.append(f"focal_call={name.lower()}")
    for receiver in sorted(set(extract_receivers(assertion))):
        tokens.append(f"assert_receiver={receiver.lower()}")
    return_var = extract_return_var(prefix, method_name)
    focal_receiver = extract_focal_receiver(prefix, method_name)
    if return_var:
        tokens.append(f"return_var={return_var.lower()}")
    if focal_receiver:
        tokens.append(f"focal_receiver={focal_receiver.lower()}")
    return tokens


def row_to_text(entry: Dict[str, Any], cand: Dict[str, Any]) -> str:
    soot = entry.get("soot_analysis_result") or {}
    assertion = cand.get("assertion") or entry.get("assert") or ""
    prefix = entry.get("prefix", "") or ""
    focal_prefix = entry.get("focal_prefix", "") or ""
    focal_method = entry.get("focal_method", "") or ""

    feat = extract_soot_features(soot)
    tokens: List[str] = []
    tokens.extend(normalize_text(assertion).split())
    tokens.extend(normalize_text(prefix[:1200]).split())
    tokens.extend(normalize_text(focal_prefix[:800]).split())
    tokens.extend(normalize_text(focal_method[:1600]).split())
    tokens.append(extract_assertion_skeleton(assertion))
    tokens.extend(assertion_signals(assertion))
    tokens.extend(text_buckets(prefix, "prefix"))
    tokens.extend(text_buckets(focal_prefix, "focal"))
    tokens.extend(text_buckets(focal_method, "method"))
    tokens.extend(context_tokens(entry, assertion))
    tokens.extend([f"{k}={v}" for k, v in extract_structured_assertion_features(assertion).items()])
    tokens.extend([f"{k}={v}" for k, v in extract_context_features(entry, assertion).items()])
    for key, value in feat.items():
        if isinstance(value, float) and float(value).is_integer():
            value = int(value)
        tokens.append(f"soot_{key}={value}")
    return " ".join(tokens)


def build_rows(data: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for entry in data:
        group_id = entry.get("id")
        soot = entry.get("soot_analysis_result") or {}
        ast_generates = entry.get("ast_generates", []) or []
        bug_counts = defaultdict(int)
        for mutant in ast_generates:
            bug_type = (mutant or {}).get("bug_type", "Unknown")
            bug_counts[bug_type] += 1
        if bug_counts:
            dominant_bug_type = max(bug_counts.items(), key=lambda item: (item[1], item[0]))[0]
            dominant_bug_ratio = float(bug_counts[dominant_bug_type]) / float(sum(bug_counts.values()))
        else:
            dominant_bug_type = "Unknown"
            dominant_bug_ratio = 0.0
        for idx, cand in enumerate(entry.get("candidate_results", []) or []):
            cand_idx = cand.get("candidate_index", idx)
            bug_meta: Dict[str, Any] = {}
            if isinstance(cand_idx, int) and 0 <= cand_idx < len(ast_generates):
                bug_meta = ast_generates[cand_idx] or {}
            elif idx < len(ast_generates):
                bug_meta = ast_generates[idx] or {}
            kill = float(cand.get("kill_count", 0) or 0)
            valid = float(cand.get("valid_mutant_count", 0) or 0)
            total = float(cand.get("total_mutants", 0) or 0)
            label = safe_div(kill, valid) if valid > 0 else 0.0
            rows.append(
                {
                    "group_id": group_id,
                    "project": entry.get("project", ""),
                    "bug_num": entry.get("bug_num", ""),
                    "bug_type": dominant_bug_type,
                    "bug_type_ratio": dominant_bug_ratio,
                    "mutation": bug_meta.get("mutation", ""),
                    "candidate_index": cand.get("candidate_index", -1),
                    "assertion": cand.get("assertion") or entry.get("assert") or "",
                    "status": cand.get("status", ""),
                    "kill_count": kill,
                    "valid_mutant_count": valid,
                    "total_mutants": total,
                    "kill_ratio": clamp01(label),
                    **extract_structured_assertion_features(cand.get("assertion") or entry.get("assert") or ""),
                    **extract_context_features(entry, cand.get("assertion") or entry.get("assert") or ""),
                    **extract_soot_features(soot),
                    "feature_text": row_to_text(entry, cand),
                    "assertion_raw": cand.get("assertion") or entry.get("assert") or "",
                }
            )
    return pd.DataFrame(rows)


NUMERIC_FEATURE_COLUMNS = [
    "assert_len",
    "assert_method_calls",
    "assert_numeric_literals",
    "assert_float_literals",
    "assert_int_literals",
    "assert_string_literals",
    "assert_null_mentions",
    "assert_boolean_mentions",
    "assert_getter_calls",
    "assert_comparison_calls",
    "assert_arith_ops",
    "assert_cmp_ops",
    "assert_plus_one",
    "assert_minus_one",
    "assert_zero",
    "assert_one",
    "assert_notnull",
    "assert_null",
    "assert_boolean",
    "assert_equals",
    "assert_notequals",
    "assert_throws",
    "assert_contains_api",
    "assert_index_api",
    "assert_string_api",
    "assert_collection_api",
    "assert_len_short",
    "assert_len_medium",
    "assert_len_long",
    "ctx_assert_method_count",
    "ctx_context_method_count",
    "ctx_api_overlap_count",
    "ctx_focal_api_overlap_count",
    "ctx_api_overlap_ratio",
    "ctx_focal_api_overlap_ratio",
    "ctx_receiver_count",
    "ctx_receiver_overlap_count",
    "ctx_receiver_overlap_ratio",
    "ctx_declared_var_count",
    "ctx_assert_uses_return_var",
    "ctx_assert_uses_focal_receiver",
    "ctx_assert_uses_any_declared_var",
    "ctx_assert_calls_focal_method",
    "ctx_assert_has_param_call",
    "ctx_focal_has_arith",
    "ctx_focal_has_cmp",
    "ctx_focal_has_index",
    "ctx_focal_has_string_api",
    "ctx_prefix_has_same_assert_api",
    "ctx_assert_null_guard",
    "ctx_assert_instanceof",
    "branches",
    "paths",
    "returns",
    "pdg_nodes",
    "units",
    "invoke_units",
    "throw_units",
    "focal_branch_state",
    "focal_exception_paths",
    "focal_branch_cond",
    "focal_pdg_dependants",
    "focal_path_constraints",
    "focal_fields_read",
    "focal_fields_modified",
    "focal_static_reads",
    "ret_const_sites",
    "ret_null_sites",
    "ret_constant_flag",
    "ret_null_flag",
]


def build_preprocessor(alpha: float) -> Pipeline:
    text_preprocessor = ColumnTransformer(
        transformers=[
            (
                "word_text",
                TfidfVectorizer(
                    lowercase=True,
                    token_pattern=r"(?u)\b\w+\b",
                    ngram_range=(1, 2),
                    min_df=2,
                    max_features=25000,
                ),
                "feature_text",
            ),
            (
                "char_assert",
                TfidfVectorizer(
                    lowercase=True,
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    min_df=2,
                    max_features=12000,
                ),
                "assertion_raw",
            ),
            (
                "numeric",
                Pipeline(
                    [
                        ("select", "passthrough"),
                        ("scale", MaxAbsScaler()),
                    ]
                ),
                NUMERIC_FEATURE_COLUMNS,
            ),
        ],
        remainder="drop",
        sparse_threshold=0.3,
    )

    return Pipeline(
        [
            ("features", text_preprocessor),
            ("ridge", Ridge(alpha=alpha, fit_intercept=True, solver="lsqr")),
        ]
    )


def _spearman(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) < 2:
        return 0.0
    true_rank = pd.Series(y_true).rank(method="average").to_numpy()
    pred_rank = pd.Series(y_pred).rank(method="average").to_numpy()
    if np.std(true_rank) == 0.0 or np.std(pred_rank) == 0.0:
        return 0.0
    return float(np.corrcoef(true_rank, pred_rank)[0, 1])


def _pairwise_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    correct = 0
    total = 0
    n = len(y_true)
    for i in range(n):
        for j in range(i + 1, n):
            if y_true[i] == y_true[j]:
                continue
            total += 1
            true_sign = 1 if y_true[i] > y_true[j] else -1
            pred_sign = 1 if y_pred[i] > y_pred[j] else -1
            if true_sign == pred_sign:
                correct += 1
    return float(correct) / float(total) if total else 0.0


def evaluate_predictions(df: pd.DataFrame, preds: np.ndarray) -> Dict[str, Any]:
    y_true = df["kill_ratio"].to_numpy(dtype=float)
    y_pred = np.asarray(preds, dtype=float)

    group_metrics = []
    oracle_rows = []
    for group_id, g in df.groupby("group_id", sort=False):
        idx = g.index.to_numpy()
        gt = g["kill_ratio"].to_numpy(dtype=float)
        pr = y_pred[idx]
        best_true_idx = int(np.argmax(gt))
        best_pred_idx = int(np.argmax(pr))
        group_metrics.append(
            {
                "group_id": group_id,
                "best_true": float(gt[best_true_idx]),
                "best_pred_true": float(gt[best_pred_idx]),
                "regret": float(np.max(gt) - gt[best_pred_idx]),
                "top1_hit": int(best_true_idx == best_pred_idx),
                "spearman": _spearman(gt, pr),
                "pairwise_acc": _pairwise_accuracy(gt, pr),
            }
        )
        oracle_rows.append(
            {
                "group_id": group_id,
                "best_bug_type": g.iloc[best_true_idx]["bug_type"],
                "best_true": float(gt[best_true_idx]),
                "best_pred_true": float(gt[best_pred_idx]),
                "regret": float(np.max(gt) - gt[best_pred_idx]),
                "top1_hit": int(best_true_idx == best_pred_idx),
            }
        )

    overall = {
        "mae": float(np.mean(np.abs(y_true - y_pred))),
        "rmse": float(math.sqrt(np.mean((y_true - y_pred) ** 2))),
        "spearman": _spearman(y_true, y_pred),
        "pairwise_acc": _pairwise_accuracy(y_true, y_pred),
        "group_spearman": float(np.mean([x["spearman"] for x in group_metrics])) if group_metrics else 0.0,
        "group_pairwise_acc": float(np.mean([x["pairwise_acc"] for x in group_metrics])) if group_metrics else 0.0,
        "group_top1_hit": float(np.mean([x["top1_hit"] for x in group_metrics])) if group_metrics else 0.0,
        "group_regret": float(np.mean([x["regret"] for x in group_metrics])) if group_metrics else 0.0,
    }

    bug_type_rows = []
    for bug_type, g in df.assign(pred=y_pred).groupby("bug_type", sort=False):
        yt = g["kill_ratio"].to_numpy(dtype=float)
        yp = g["pred"].to_numpy(dtype=float)
        bug_type_rows.append(
            {
                "bug_type": bug_type,
                "n": int(len(g)),
                "mae": float(np.mean(np.abs(yt - yp))),
                "rmse": float(math.sqrt(np.mean((yt - yp) ** 2))),
                "spearman": _spearman(yt, yp),
                "pairwise_acc": _pairwise_accuracy(yt, yp),
                "mean_true": float(np.mean(yt)),
                "mean_pred": float(np.mean(yp)),
            }
        )

    oracle_bug_rows = []
    oracle_df = pd.DataFrame(oracle_rows)
    for bug_type, g in oracle_df.groupby("best_bug_type", sort=False):
        oracle_bug_rows.append(
            {
                "bug_type": bug_type,
                "groups": int(len(g)),
                "mean_best_true": float(np.mean(g["best_true"])),
                "mean_best_pred_true": float(np.mean(g["best_pred_true"])),
                "mean_regret": float(np.mean(g["regret"])),
                "top1_hit": float(np.mean(g["top1_hit"])),
            }
        )

    return {
        "overall": overall,
        "by_bug_type": pd.DataFrame(bug_type_rows).sort_values(
            ["spearman", "mean_regret"] if "mean_regret" in pd.DataFrame(bug_type_rows).columns else ["spearman"],
            ascending=[True, False] if "mean_regret" in pd.DataFrame(bug_type_rows).columns else [True],
        ).to_dict(orient="records"),
        "oracle_by_bug_type": pd.DataFrame(oracle_bug_rows).sort_values(
            ["mean_regret", "top1_hit"], ascending=[False, True]
        ).to_dict(orient="records"),
        "group_rows": group_metrics,
        "oracle_rows": oracle_rows,
    }


def choose_alpha(df: pd.DataFrame, alphas: Sequence[float], folds: int) -> Dict[str, Any]:
    groups = df["group_id"].to_numpy()
    y = df["kill_ratio"].to_numpy(dtype=float)
    splitter = GroupKFold(n_splits=folds)
    records: List[Dict[str, Any]] = []

    for alpha in alphas:
        preds = np.zeros(len(df), dtype=float)
        for train_idx, test_idx in splitter.split(df, y, groups):
            model = build_preprocessor(alpha)
            train_df = df.iloc[train_idx]
            test_df = df.iloc[test_idx]
            model.fit(train_df, y[train_idx])
            preds[test_idx] = model.predict(test_df)
        metrics = evaluate_predictions(df, preds)
        records.append(
            {
                "alpha": alpha,
                "mae": metrics["overall"]["mae"],
                "rmse": metrics["overall"]["rmse"],
                "spearman": metrics["overall"]["spearman"],
                "group_spearman": metrics["overall"]["group_spearman"],
                "group_pairwise_acc": metrics["overall"]["group_pairwise_acc"],
                "group_top1_hit": metrics["overall"]["group_top1_hit"],
                "group_regret": metrics["overall"]["group_regret"],
            }
        )

    summary = pd.DataFrame(records)
    best_row = summary.sort_values(
        ["group_spearman", "group_top1_hit", "spearman"], ascending=[False, False, False]
    ).iloc[0]
    return {
        "summary": summary,
        "best_alpha": float(best_row["alpha"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a static baseline reward model.")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--alphas", default="0.1,1.0,5.0,10.0")
    args = parser.parse_args()

    data = load_json(args.input)
    df = build_rows(data)
    if df.empty:
        raise SystemExit("No candidate rows found in input JSON.")
    df = df.copy()
    for col in NUMERIC_FEATURE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    for col in ["feature_text", "assertion_raw"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    os.makedirs(args.output_dir, exist_ok=True)

    alpha_values = [float(x.strip()) for x in args.alphas.split(",") if x.strip()]
    tuning = choose_alpha(df, alpha_values, folds=args.folds)
    summary_df = tuning["summary"]
    best_alpha = tuning["best_alpha"]

    print("Alpha sweep:")
    print(summary_df.to_string(index=False))
    print(f"\nChosen alpha: {best_alpha}")

    groups = df["group_id"].to_numpy()
    y = df["kill_ratio"].to_numpy(dtype=float)
    splitter = GroupKFold(n_splits=args.folds)
    oof_preds = np.zeros(len(df), dtype=float)
    for train_idx, test_idx in splitter.split(df, y, groups):
        model = build_preprocessor(best_alpha)
        train_df = df.iloc[train_idx]
        test_df = df.iloc[test_idx]
        model.fit(train_df, y[train_idx])
        oof_preds[test_idx] = model.predict(test_df)

    oof_metrics = evaluate_predictions(df, oof_preds)
    print("\nOverall metrics:")
    print(json.dumps(oof_metrics["overall"], indent=2))

    by_bug_type = pd.DataFrame(oof_metrics["by_bug_type"])
    oracle_by_bug_type = pd.DataFrame(oof_metrics["oracle_by_bug_type"])
    print("\nCandidate metrics by bug_type (worst first):")
    print(by_bug_type.head(20).to_string(index=False))
    print("\nOracle group regret by bug_type (worst first):")
    print(oracle_by_bug_type.head(20).to_string(index=False))

    preds_df = df.copy()
    preds_df["prediction"] = oof_preds
    preds_df["error"] = preds_df["prediction"] - preds_df["kill_ratio"]
    preds_path = os.path.join(args.output_dir, "oof_predictions.csv")
    preds_df.to_csv(preds_path, index=False)

    final_model = build_preprocessor(best_alpha)
    final_model.fit(df, y)

    bundle = {
        "model": final_model,
        "alpha": best_alpha,
        "preprocessor": final_model.named_steps["features"],
        "regressor": final_model.named_steps["ridge"],
        "overall_metrics": oof_metrics["overall"],
        "by_bug_type": oof_metrics["by_bug_type"],
        "oracle_by_bug_type": oof_metrics["oracle_by_bug_type"],
        "feature_schema": {
            "text": "word ngrams over combined text; char ngrams over assertion; structured numeric features",
            "label": "kill_count / valid_mutant_count",
        },
    }
    model_path = os.path.join(args.output_dir, "static_reward_baseline.pkl")
    with open(model_path, "wb") as f:
        pickle.dump(bundle, f)

    metrics_path = os.path.join(args.output_dir, "metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "alpha_sweep": summary_df.to_dict(orient="records"),
                "overall": oof_metrics["overall"],
                "by_bug_type": oof_metrics["by_bug_type"],
                "oracle_by_bug_type": oof_metrics["oracle_by_bug_type"],
                "predictions_csv": preds_path,
                "model_path": model_path,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"\nSaved predictions to: {preds_path}")
    print(f"Saved model to: {model_path}")
    print(f"Saved metrics to: {metrics_path}")


if __name__ == "__main__":
    main()
