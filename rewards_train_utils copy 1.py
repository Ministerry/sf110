import json
import numpy as np
import scipy.stats as stats
import itertools
import os
import javalang
from openai import OpenAI, APIConnectionError
from config import DEEPSEEK_API_KEY
import re
import math
weights = {
    "role_return_no_side_effects": 34.0,
    # Full-set tuning shows side-effectful return checks still need substantial
    # emphasis when combined with reduced boundary bias.
    "role_return_with_side_effects": 24.0,
    "role_focal": 11.0,
    "role_input": 5.0,
    "role_other": 2.0,
    "mod_new": 25.0,
    "mod_accessible": 20.0,
    "mod_field": 10.0,
    "mod_none": 0.0,
    "impact_branch_weight": 1.0,
    "impact_cap": 25,
    "impact_nonmodified_multiplier": 0.2,
    "complexity_fields": 1.0,
    "complexity_elements": 0.75,
    "complexity_deep_fields": 1.25,
    "complexity_static_fields": 0.5,
    "bonus_return_no_side_effects": 5.0,
    "penalty_focal_return_unmodified": -3.0,
    "branch_condition_multiplier": 0.6,
    "pdg_per_dep": 0.25,
    "pdg_bonus_cap": 5.0,
    "logic_const_match_bonus": 8.0,
    "logic_size_empty_bonus": 8.0,
    "logic_comp_bonus": 8.0,
    "logic_exception_bonus": 6.0,
    "logic_return_bonus": 4.0,
    # Full-set tuning prefers stronger exact-return encouragement.
    "contract_exact_return_bonus": 6.0,
    "contract_exact_state_bonus": 2.5,
    # Boundary checks remain useful, but on the full set they should stay weak
    # relative to exact-return contracts.
    "contract_boundary_bonus": 0.75,
    "contract_tight_comparison_bonus": 2.5,
    "contract_loose_exclusion_penalty": -2.5,
    "contract_to_string_penalty": -3.0,
    "contract_observational_penalty": -1.5,
    "logic_cap": 20.0,
    "logic_alpha": 0.20,
    "trivial_assertion_multiplier": 0.1,
    "semantic_center": 50.0,
    "semantic_scale": 0.15,
    "semantic_mapping": "sigmoid",
    # LLM semantic reward fusion. Heuristic still dominates, but semantic
    # judgment now contributes to the returned reward when available.
    "fusion_w_heuristic": 0.75,
    "fusion_w_semantic": 0.25,
    # Training reward should preserve ranking direction while avoiding the
    # heavy saturation caused by the evaluation-oriented sigmoid alone.
    # RL reward should keep more headroom in the upper range. We therefore use
    # a mostly cap-relative linear mapping instead of the heavily saturated
    # sigmoid that is convenient for evaluation summaries.
    # [Optimization] Reduced cap_multiplier and re-enabled sigmoid component
    # to provide better gradient near the 0.5 decision point.
    "train_reward_ratio_weight": 0.80,
    "train_reward_sigmoid_weight": 0.20,
    "train_reward_var_weight": 0.00,
    "train_reward_cap_multiplier": 1.5,
    "logistic_k": 1.0,
    # fraction of logic_cap used to clamp label bonuses to avoid over-influence
    "label_bonus_clamp_frac": 0.1
}

# Default syntax-label priors are disabled in the current best setting.
# Targeted sweeps showed that even weak syntax priors can hurt ranking quality,
# so we keep this channel neutral and rely on contract sensitivity, variable
# signals, logic alignment, and subtype/context signals instead.
LABEL_DEFAULT_BONUSES = {
    "assertEquals-primitive": 0.0,
    "assertEquals-collection_size": 0.0,
    "assertEquals-constructor": 0.0,
    "assertTrue-comparison_expr": 0.0,
    "assertFalse-comparison_expr": 0.0,
    "assertThrows-exception": 0.0,
    "assertNull": 0.0,
    "assertNotNull": 0.0,
    "unknown": 0.0,
    "assertThat-collection_size": 0.0,
    "assertThat-empty": 0.0,
    "assertThat-contains": 0.0,
    "assertThat-equalTo": 0.0,
    "assertThat-instanceOf": 0.0,
    "assertThat-other": 0.0
}
for _k, _v in LABEL_DEFAULT_BONUSES.items():
    weights.setdefault(f"label_bonus_{_k}", _v)

# Direct additive subtype bonuses are also disabled in the current best setting.
# We still keep semantic subtype inference itself, because downstream rules
# (especially contract sensitivity) use it as a routing signal.
SEMANTIC_SUBTYPE_DEFAULT_BONUSES = {
    "return_value_exact": 0.0,
    "return_value_comparison": 0.0,
    "collection_observable": 0.0,
    "state_getter_check": 0.0,
    "state_constraint_invariant": 0.0,
    "state_observable": 0.0,
    "exception_contract": 0.0,
    "null_contract_guard": 0.0,
    "observable_nullness": 0.0,
    "smoke_nullness": 0.0,
    "constructor_smoke": 0.0,
    "generic_observable": 0.0,
}
for _k, _v in SEMANTIC_SUBTYPE_DEFAULT_BONUSES.items():
    weights.setdefault(f"semantic_bonus_{_k}", _v)

# Keep semantic LLM reward fully disabled for static-analysis-only runs.
SEMANTIC_REWARD_ENABLED = False


def infer_assertion_semantic_subtype(assertion, assertion_label, assertion_scores,
                                     comparisons=None, has_any_constraints=False,
                                     constraint_alignment_hits=0):
    comparisons = comparisons or []
    low = assertion.lower()
    has_return = False
    has_modified = False
    has_impact = False
    has_complexity = False
    for vd in assertion_scores.values():
        if not isinstance(vd, dict):
            continue
        role = vd.get("role", "") or ""
        if "Return Value" in role:
            has_return = True
        bd = vd.get("breakdown", {}) if isinstance(vd.get("breakdown", {}), dict) else {}
        if bd.get("Modification", 0) > 0:
            has_modified = True
        if bd.get("Impact", 0) > 0:
            has_impact = True
        if bd.get("Complexity", 0) > 0:
            has_complexity = True

    has_state_signal = has_modified or has_impact or has_complexity
    has_constraint_support = constraint_alignment_hits > 0
    has_member_call = bool(re.search(r'\.[A-Za-z_][A-Za-z0-9_]*\s*\(', assertion))
    has_size_signal = bool(re.search(r'\b(size|length)\s*\(|\.length\b|isempty|empty\b', low))
    has_null_signal = bool(re.search(r'\bnull\b|assert(?:not)?null|assertsame', low))

    if assertion_label == "assertThrows-exception":
        return "exception_contract"
    if assertion_label in {"assertNull", "assertNotNull"}:
        if has_constraint_support or (has_any_constraints and has_null_signal):
            return "null_contract_guard"
        if has_return or has_state_signal:
            return "observable_nullness"
        return "smoke_nullness"
    if "collection_size" in assertion_label or assertion_label == "assertThat-empty" or has_size_signal:
        return "collection_observable"
    if assertion_label == "assertEquals-constructor":
        return "constructor_smoke"
    if has_return:
        if "comparison_expr" in assertion_label or comparisons:
            return "return_value_comparison"
        return "return_value_exact"
    if has_state_signal and has_constraint_support:
        return "state_constraint_invariant"
    if has_state_signal and has_member_call:
        return "state_getter_check"
    if has_state_signal:
        return "state_observable"
    return "generic_observable"


def compute_contextual_label_bonus(assertion_label, semantic_subtype, assertion_scores,
                                   has_any_constraints=False, constraint_alignment_hits=0,
                                   comparisons=None):
    """Use syntax type + semantic subtype as weak priors, then strengthen them with context."""
    try:
        label_base = float(weights.get(f'label_bonus_{assertion_label}', 0.0))
    except Exception:
        label_base = 0.0
    try:
        semantic_base = float(weights.get(f'semantic_bonus_{semantic_subtype}', 0.0))
    except Exception:
        semantic_base = 0.0

    base = label_base + semantic_base

    if abs(base) < 1e-12:
        return 0.0

    comparisons = comparisons or []
    informative_labels = {
        "assertEquals-primitive",
        "assertEquals-collection_size",
        "assertTrue-comparison_expr",
        "assertFalse-comparison_expr",
        "assertThrows-exception",
        "assertThat-collection_size",
        "assertThat-empty",
        "assertThat-contains",
        "assertThat-equalTo",
    }
    collection_labels = {
        "assertEquals-collection_size",
        "assertThat-collection_size",
        "assertThat-empty",
    }

    if not assertion_scores:
        # If we failed to match variables, fall back to a very small prior only.
        return base * 0.25

    has_return = False
    has_modified = False
    has_impact = False
    has_complexity = False
    for vd in assertion_scores.values():
        if not isinstance(vd, dict):
            continue
        role = vd.get("role", "") or ""
        if "Return Value" in role:
            has_return = True
        bd = vd.get("breakdown", {}) if isinstance(vd.get("breakdown", {}), dict) else {}
        if bd.get("Modification", 0) > 0:
            has_modified = True
        if bd.get("Impact", 0) > 0:
            has_impact = True
        if bd.get("Complexity", 0) > 0:
            has_complexity = True

    has_state_signal = has_modified or has_impact or has_complexity
    has_constraint_support = constraint_alignment_hits > 0

    strength = 0.15
    if assertion_label in informative_labels:
        strength += 0.10
    if assertion_label in collection_labels:
        strength += 0.10
    if assertion_label == "assertThrows-exception":
        strength += 0.15
    if semantic_subtype in {"return_value_exact", "return_value_comparison", "collection_observable", "exception_contract"}:
        strength += 0.10
    if semantic_subtype in {"state_getter_check", "state_constraint_invariant"}:
        strength += 0.10
    if "comparison_expr" in assertion_label and comparisons:
        strength += 0.10
    if has_return:
        strength += 0.15
    if has_state_signal:
        strength += 0.20
    if has_any_constraints:
        strength += 0.10
    if has_constraint_support:
        strength += 0.25 + min(0.05 * max(0, constraint_alignment_hits - 1), 0.15)

    if assertion_label in {"assertNull", "assertNotNull"}:
        if has_constraint_support or has_state_signal:
            # Soften null/not-null penalties when they are actually supported.
            strength *= 0.35
        else:
            strength = max(strength, 0.9)
    if semantic_subtype in {"smoke_nullness", "constructor_smoke"} and not has_constraint_support:
        strength = max(strength, 0.85)

    return base * max(0.0, strength)


def compute_contract_sensitivity_bonus(assertion, assertion_label, semantic_subtype,
                                       assertion_scores, comparisons=None):
    """Reward precise, mutation-sensitive contracts over broad observational checks."""
    comparisons = comparisons or []
    low = assertion.lower()
    bonus = 0.0

    has_return = False
    has_state_signal = False
    for vd in assertion_scores.values():
        if not isinstance(vd, dict):
            continue
        role = vd.get("role", "") or ""
        if "Return Value" in role:
            has_return = True
        bd = vd.get("breakdown", {}) if isinstance(vd.get("breakdown", {}), dict) else {}
        if bd.get("Modification", 0) > 0 or bd.get("Impact", 0) > 0:
            has_state_signal = True

    # Exact contract checks are often stronger than richer observational checks.
    is_exact_api = assertion_label in {
        "assertEquals-primitive",
        "assertThat-equalTo",
        "assertSame",
        "assertNotSame",
    } or bool(re.search(r'\bassertequals\b|\bequalto\s*\(', low))
    if is_exact_api:
        if has_return:
            bonus += weights.get("contract_exact_return_bonus", 5.0)
        elif has_state_signal:
            bonus += weights.get("contract_exact_state_bonus", 2.5)

    boundary_literals = re.findall(
        r'\b(?:-1|0|1|null|true|false|integer\.min_value|integer\.max_value|long\.min_value|long\.max_value)\b',
        low
    )
    if boundary_literals:
        bonus += min(
            len(boundary_literals) * weights.get("contract_boundary_bonus", 3.0) * 0.5,
            weights.get("contract_boundary_bonus", 3.0)
        )

    tight_ops = {"==", ">=", "<=", ">", "<"}
    if comparisons and any((len(c) >= 2 and str(c[1]) in tight_ops) for c in comparisons):
        bonus += weights.get("contract_tight_comparison_bonus", 2.5)

    # Penalize looser “not this, not that” checks that exclude a few values
    # without pinning down the core contract.
    if ("assertfalse" in low or "asserttrue" in low) and any(op == "!=" for lhs, op, rhs in comparisons if lhs is not None or rhs is not None):
        bonus += weights.get("contract_loose_exclusion_penalty", -2.5)
    if re.search(r'assert(?:true|false)\s*\([^)]*(==|!=)[^)]*(\|\||&&)[^)]*(==|!=)', low):
        bonus += weights.get("contract_loose_exclusion_penalty", -2.5)
    if re.search(r'\bnot\s*\(|\bis\s*\(\s*not\s*\(', low):
        bonus += 0.5 * weights.get("contract_loose_exclusion_penalty", -2.5)

    # toString-style checks are often observationally rich but brittle and indirect.
    if ".tostring(" in low:
        bonus += weights.get("contract_to_string_penalty", -3.0)

    # Collection/state observation can be useful, but if it does not pin a precise
    # value or boundary then it should not outrank a precise return-value contract.
    if semantic_subtype in {"state_getter_check", "state_observable"} and not boundary_literals and not comparisons:
        bonus += weights.get("contract_observational_penalty", -1.5)

    return bonus

def get_recommendation(var_name, scores, role, soot_var=None, dynamic_diff=None):
    
    recs = []
    if "Return Value" in role:
        recs.append("MUST: Assert return value (assertEquals/True/False).")
    
    if scores["Modification"] > 5 and "Return Value" not in role:
        state_access = (soot_var or {}).get("data_flow_state_access", {})
        if not isinstance(state_access, dict):
            state_access = {}
        static_writes = state_access.get("static_fields_modified", 0)

        if not isinstance(dynamic_diff, dict):
            dynamic_diff = {}
        field_changes = dynamic_diff.get("field_changes", {})
        if not isinstance(field_changes, dict):
            field_changes = {}
        deep_changes = dynamic_diff.get("deep_changes", {})
        if not isinstance(deep_changes, dict):
            deep_changes = {}
        element_changes = dynamic_diff.get("element_changes", {})
        if not isinstance(element_changes, dict):
            element_changes = {}
        value_change = (dynamic_diff or {}).get("value_change") if dynamic_diff else None
        array_length_change = (dynamic_diff or {}).get("array_length_change") if dynamic_diff else None
        collection_size_change = (dynamic_diff or {}).get("collection_size_change") if dynamic_diff else None
        has_any_change = bool(field_changes or deep_changes or element_changes or value_change or array_length_change or collection_size_change)

        accessible_changes = [f for f, c in field_changes.items() if isinstance(c, dict) and c.get("getter")]
        inaccessible_changes = [f for f, c in field_changes.items() if not (isinstance(c, dict) and c.get("getter"))]

        if collection_size_change:
            recs.append("MUST: Assert collection/map size change.")
        if accessible_changes:
            recs.append("MUST: Assert state change via public getter(s).")
        elif has_any_change:
            recs.append("RISK: State changed but no getter-mapped field detected; avoid private-field assertions.")
        else:
            recs.append("MUST: Assert state change (fields changed).")

        if inaccessible_changes:
            recs.append("NOTE: Some changed fields are not getter-accessible.")

        if static_writes > 0:
            recs.append("MUST: Assert and clean up static/global state to avoid test pollution.")

        if "Input Argument" in role:
            recs.append("SHOULD: Assert side effects on input argument object state.")
        
    if scores["Impact"] > 0 and scores["Modification"] == 0:
        recs.append("SHOULD: Assert invariant (state used in logic maintained).")
        
    if scores["Complexity"] >= 4:
        recs.append(f"hint: Deep assertion needed (check fields/elements of {var_name}).")
        
    if not recs:
        recs.append("LOW: Low priority for assertion.")
        
    return " ".join(recs)

def quantify_assertion_value(soot_data_wrapped, dynamic_data):
    results = {}
    if isinstance(soot_data_wrapped, dict) and "error" in soot_data_wrapped:
        return {"error": soot_data_wrapped["error"]}
    if isinstance(dynamic_data, dict) and "error" in dynamic_data:
        return {"error": dynamic_data["error"]}

    if not isinstance(dynamic_data, dict):
        dynamic_data = {}

    if isinstance(soot_data_wrapped, dict) and "variables" in soot_data_wrapped:
        soot_data = soot_data_wrapped["variables"]
        global_info = soot_data_wrapped.get("global_metrics", {})
    else:
        soot_data = soot_data_wrapped if isinstance(soot_data_wrapped, list) else []
        global_info = {}

    dynamic_diff = dynamic_data.get("diff")
    if not isinstance(dynamic_diff, dict):
        dynamic_diff = {}
    dynamic_after_all = dynamic_data.get("after")
    if not isinstance(dynamic_after_all, dict):
        dynamic_after_all = {}

    # Check for global side effects (any variable modified)
    has_side_effects = len(dynamic_diff) > 0
    # Build a unified variable map from both Soot and Dynamic Analysis
    soot_vars_map = {
        v["variable"]: v
        for v in soot_data
        if isinstance(v, dict) and isinstance(v.get("variable"), str)
    }
    dynamic_diff_vars = dynamic_diff
    all_varnames = set(soot_vars_map.keys()) | set(dynamic_diff_vars.keys())

    # --- New Logic: Return Value Inheritance (Optimization 1) ---
    # If a variable is a Return Value, it should inherit path_constraints from the Focal Object (THIS)
    # because the return value's existence/state often depends on the Focal Object's logic.
    focal_vars = [v for v, info in soot_vars_map.items() if "Focal Object" in info.get("role", "")]
    if focal_vars:
        focal_constraints = []
        for fv in focal_vars:
            fv_cfi = soot_vars_map[fv].get("control_flow_influence", {})
            if not isinstance(fv_cfi, dict):
                fv_cfi = {}
            path_constraints = fv_cfi.get("path_constraints", [])
            if not isinstance(path_constraints, list):
                path_constraints = []
            focal_constraints.extend(path_constraints)
        
        for var_name in all_varnames:
            info = soot_vars_map.get(var_name, {})
            if "Return Value" in info.get("role", ""):
                if "control_flow_influence" not in info or not isinstance(info.get("control_flow_influence"), dict):
                    info["control_flow_influence"] = {}
                existing = info["control_flow_influence"].get("path_constraints", [])
                if not isinstance(existing, list):
                    existing = []
                # Inherit focal constraints if the return value doesn't have its own or to supplement them
                info["control_flow_influence"]["path_constraints"] = list(set(existing + focal_constraints))

    # For bonus/penalty
    all_roles = set([soot_vars_map.get(v, {}).get("role", "") for v in all_varnames])

    for var_name in all_varnames:
        soot_var = soot_vars_map.get(var_name, {})
        score_breakdown = {"Role": 0, "Modification": 0, "Impact": 0, "Complexity": 0}
        role = soot_var.get("role", "Implicit/Environment State")

        # 1. Role (use preset weights)
        if "Return Value" in role:
            score_breakdown["Role"] = weights["role_return_no_side_effects"] if not has_side_effects else weights["role_return_with_side_effects"]
        elif "Focal Object" in role:
            score_breakdown["Role"] = weights["role_focal"]
        elif "Input Argument" in role or "Constructor Argument" in role:
            score_breakdown["Role"] = weights["role_input"]
        else:
            score_breakdown["Role"] = weights["role_other"]

        # 2. Modification
        is_modified = False
        if var_name in dynamic_diff:
            diff_info = dynamic_diff.get(var_name, {})
            if not isinstance(diff_info, dict):
                diff_info = {}
            if diff_info.get("type") == "new":
                score_breakdown["Modification"] = weights["mod_new"]
            else:
                # Accessible modification (getter/array/collection/element)
                field_changes = diff_info.get("field_changes", {})
                if not isinstance(field_changes, dict):
                    field_changes = {}
                element_changes = diff_info.get("element_changes", {})
                if not isinstance(element_changes, dict):
                    element_changes = {}
                accessible = any(c.get("getter") for c in field_changes.values())
                if accessible or diff_info.get("collection_size_change") or diff_info.get("array_length_change") or len(element_changes) > 0:
                    score_breakdown["Modification"] = weights["mod_accessible"]
                elif field_changes:
                    score_breakdown["Modification"] = weights["mod_field"]
                else:
                    score_breakdown["Modification"] = weights["mod_none"]
            is_modified = True
        else:
            score_breakdown["Modification"] = weights["mod_none"]

        # 3. Impact
        cf_influence = soot_var.get("control_flow_influence", {})
        if not isinstance(cf_influence, dict):
            cf_influence = {}
        branches = cf_influence.get("branch_decisions_dependent_on_state", 0) + cf_influence.get("branch_decisions", 0)
        # Extra weight for specific usage in branch conditions (Logic Constraint)
        branch_cond_bonus = cf_influence.get("branch_condition_usage", 0) * weights.get("branch_condition_multiplier", 0.5)
        # PDG Influence: credit based on number of dependent nodes in the PDG
        pdg_bonus = min(cf_influence.get("pdg_dependants", 0) * weights.get("pdg_per_dep", 0.2), weights.get("pdg_bonus_cap", 5.0))
        
        exception_paths = cf_influence.get("exception_paths", 0)
        flow_influence = soot_var.get("flow_influence", {})
        if not isinstance(flow_influence, dict):
            flow_influence = {}
        branches_init = flow_influence.get("initializes_state_used_in_branches", 0)
        total_branches = branches + branches_init + (exception_paths * 1) + branch_cond_bonus + pdg_bonus
        raw_impact = min(total_branches * weights["impact_branch_weight"], weights["impact_cap"])
        if is_modified or ("Return Value" in role):
            score_breakdown["Impact"] = raw_impact
        else:
            score_breakdown["Impact"] = raw_impact * weights["impact_nonmodified_multiplier"]

        # 4. Complexity
        after_state = dynamic_after_all.get(var_name, {})
        if not isinstance(after_state, dict):
            after_state = {}
        fields = after_state.get("fields", {})
        if not isinstance(fields, dict):
            fields = {}
        deep_fields = after_state.get("deep_fields", {})
        if not isinstance(deep_fields, dict):
            deep_fields = {}
        elements = after_state.get("elements", {})
        if not isinstance(elements, dict):
            elements = {}
        state_access = soot_var.get("data_flow_state_access", {})
        if not isinstance(state_access, dict):
            state_access = {}
        static_fields_list = state_access.get("static_fields_list", [])
        if not isinstance(static_fields_list, list):
            static_fields_list = []
        if len(fields) > 0:
            score_breakdown["Complexity"] += weights["complexity_fields"]
        if len(elements) > 0:
            score_breakdown["Complexity"] += weights["complexity_elements"]
        if len(deep_fields) > 0:
            score_breakdown["Complexity"] += weights["complexity_deep_fields"]
        if len(static_fields_list) > 0:
            score_breakdown["Complexity"] += weights["complexity_static_fields"]

        # 5. Bonus/Penalty
        bonus = 0
        if "Return Value" in role and not has_side_effects:
            bonus += weights["bonus_return_no_side_effects"]
        if "Focal Object" in role and "Return Value" in all_roles and not is_modified:
            bonus += weights["penalty_focal_return_unmodified"]

        total_score = sum(score_breakdown.values()) + bonus
        results[var_name] = {
            "total_score": total_score,
            "breakdown": score_breakdown,
            "role": role,
            "recommendation": get_recommendation(
                var_name,
                score_breakdown,
                role,
                soot_var=soot_var,
                dynamic_diff=dynamic_diff.get(var_name, {})
            )
        }

    scores = [v['total_score'] for v in results.values()] if results else [0.0]
    min_s = min(scores)
    max_s = max(scores)
    mean_s = float(np.mean(scores))
    std_s = float(np.std(scores))
    k = float(weights.get('logistic_k', 1.0))
    eps = 1e-8
    # optional winsorization percentage to reduce outlier influence
    winsorize_pct = float(weights.get('winsorize_pct', 0.0))

    # Optionally apply winsorization to raw scores before computing mean/std
    scores_for_stats = list(scores)
    try:
        if winsorize_pct and winsorize_pct > 0:
            lo = np.percentile(scores_for_stats, winsorize_pct)
            hi = np.percentile(scores_for_stats, 100.0 - winsorize_pct)
            scores_for_stats = [min(max(s, lo), hi) for s in scores_for_stats]
    except Exception:
        # if percentile computation fails, continue with raw scores
        scores_for_stats = list(scores)

    mean_s = float(np.mean(scores_for_stats))
    std_s = float(np.std(scores_for_stats))

    if std_s > eps:
        for _, v in results.items():
            z = (v['total_score'] - mean_s) / std_s
            # logistic (sigmoid) mapping to (0,1)
            v['normalized_total'] = 1.0 / (1.0 + math.exp(-k * z))
    else:
        # fallback to min-max if std is too small
        if max_s > min_s:
            for _, v in results.items():
                v['normalized_total'] = (v['total_score'] - min_s) / (max_s - min_s)
        else:
            # All scores equal or insufficient variance — use a heuristic sigmoid mapping
            # to provide per-variable discrimination based on a logic-cap heuristic.
            heuristic_cap = max(1.0, weights.get('logic_cap', 20.0))
            mid = 0.5 * heuristic_cap
            scale = max(1.0, heuristic_cap / 4.0)
            k_sig = float(weights.get('logistic_k', 1.0))
            # To avoid producing identical normalized values for all variables (which
            # breaks per-entry Spearman/Kendall), add a tiny deterministic tie-breaker
            # based on the variable name hash so ordering is stable but minimal.
            for name, v in results.items():
                z = (v['total_score'] - mid) / scale
                tie = (abs(hash(name)) % 1000) / 1e6  # in range [0,0.001)
                # apply tie-breaker to z (very small) before sigmoid so that values
                # are no longer exactly equal while preserving relative meaning
                z = z + (tie if v['total_score'] >= mid else -tie)
                v['normalized_total'] = 1.0 / (1.0 + math.exp(-k_sig * z))

    # Sort primarily by normalized_total (descending) but keep original total_score too
    sorted_results = dict(sorted(results.items(), key=lambda item: item[1].get('normalized_total', item[1]['total_score']), reverse=True))
    return sorted_results

def extract_assertion_label(assertion: str, var_names=None, comparisons=None) -> str:
    """
    Heuristic extraction of an assertion type label from an assertion string.
    Returns labels like:
      - assertEquals-primitive
      - assertEquals-collection_size
      - assertEquals-constructor
      - assertTrue-comparison_expr
      - assertFalse-comparison_expr
      - assertThrows-exception
      - assertNull
      - assertNotNull
      - unknown
    Uses simple regexes, presence of size/length/isEmpty, literals, `new`, and parsed comparisons.
    """
    try:
        if var_names is None:
            var_names = []
        if comparisons is None:
            comparisons = []
        api_match = re.search(r'\b(assertEquals|assertArrayEquals|assertTrue|assertFalse|assertNull|assertNotNull|assertThrows|assertThat|assertSame|assertNotSame)\b', assertion)
        api = api_match.group(1) if api_match else None
        low = assertion.lower()
        has_size = bool(re.search(r'\b(size|length)\s*\(|\.length\b|isempty', low))
        has_new = bool(re.search(r'\bnew\s+[A-Za-z_]', assertion))
        # literal detection: strings, numbers, booleans, null
        has_literal = bool(re.search(r'".*?"|\'.*?\'|\b\d+(?:\.\d+)?\b|\btrue\b|\bfalse\b|\bnull\b', low))
        # comparisons list contains tuples like (left_id, operator, right_val)
        has_comparison = any((c and len(c) >= 2 and str(c[1]) in ('>', '<', '>=', '<=', '==', '!=')) for c in (comparisons or []))

        if api in ('assertEquals', 'assertArrayEquals', 'assertSame', 'assertNotSame'):
            if has_size:
                return 'assertEquals-collection_size'
            if has_new:
                return 'assertEquals-constructor'
            if has_literal:
                return 'assertEquals-primitive'
            return 'assertEquals-primitive'

        if api in ('assertTrue', 'assertFalse'):
            if has_comparison:
                return f"{api}-comparison_expr"
            if has_size:
                return f"{api}-collection_size"
            return f"{api}-comparison_expr"

        if api == 'assertThrows':
            return 'assertThrows-exception'
        if api == 'assertNull':
            return 'assertNull'
        if api == 'assertNotNull':
            return 'assertNotNull'
        # Hamcrest / assertThat matching: try to extract matcher expression (last arg)
        if api == 'assertThat' or re.search(r'\bassertThat\b', assertion, re.IGNORECASE):
            try:
                # find the parentheses content for the assertThat call
                at_pos = re.search(r'\bassertThat\s*\(', assertion, re.IGNORECASE)
                if at_pos:
                    start = at_pos.start()
                    open_idx = assertion.find('(', start)
                    # find matching closing parenthesis
                    depth = 0
                    end_idx = None
                    for i in range(open_idx, len(assertion)):
                        if assertion[i] == '(':
                            depth += 1
                        elif assertion[i] == ')':
                            depth -= 1
                            if depth == 0:
                                end_idx = i
                                break
                    if end_idx:
                        inner = assertion[open_idx+1:end_idx]
                        # split top-level args (respect nested parentheses and quotes)
                        args = []
                        cur = ''
                        depth = 0
                        in_str = False
                        str_char = None
                        esc = False
                        for ch in inner:
                            if esc:
                                cur += ch
                                esc = False
                                continue
                            if ch == '\\':
                                cur += ch
                                esc = True
                                continue
                            if in_str:
                                cur += ch
                                if ch == str_char:
                                    in_str = False
                                continue
                            if ch in ('"', "'"):
                                in_str = True
                                str_char = ch
                                cur += ch
                                continue
                            if ch == '(':
                                depth += 1
                                cur += ch
                                continue
                            if ch == ')':
                                depth -= 1
                                cur += ch
                                continue
                            if ch == ',' and depth == 0:
                                args.append(cur.strip())
                                cur = ''
                                continue
                            cur += ch
                        if cur.strip():
                            args.append(cur.strip())
                        # matcher is usually the last argument
                        if args:
                            matcher = args[-1]
                            mlow = matcher.lower()
                            # detect common hamcrest matchers
                            if re.search(r'\bhs?size\b|has\s*size\(|hassize\(', mlow) or re.search(r'\bsize\b', mlow) and re.search(r'\d', mlow):
                                return 'assertThat-collection_size'
                            if re.search(r'isempty\b|\bempty\b|emptycollection', mlow):
                                return 'assertThat-empty'
                            if re.search(r'containsinanyorder|contains\(|hasitems?\(|hasitem\(|containsinanyorder', mlow):
                                return 'assertThat-contains'
                            if re.search(r'equalto\(|\bis\s*\(', mlow):
                                return 'assertThat-equalTo'
                            if re.search(r'instanceof\(|instanceof\b', mlow):
                                return 'assertThat-instanceOf'
                            # fallback for other hamcrest expressions
                            return 'assertThat-other'
            except Exception:
                pass
    except Exception:
        pass
    return 'unknown'


def _extract_id_from_expr(expr):
    """Recursively extract the left-most/base identifier from an expression."""
    if expr is None:
        return None
    if isinstance(expr, str):
        return expr
    if isinstance(expr, javalang.tree.MemberReference):
        qual = getattr(expr, 'qualifier', None)
        if qual:
            return _extract_id_from_expr(qual)
        return getattr(expr, 'member', None)
    if isinstance(expr, javalang.tree.MethodInvocation):
        return _extract_id_from_expr(getattr(expr, 'qualifier', None))
    try:
        q = getattr(expr, 'qualifier', None)
        if q:
            return _extract_id_from_expr(q)
        name = getattr(expr, 'member', None) or getattr(expr, 'name', None)
        if isinstance(name, str):
            return name
    except Exception:
        return None
    return None


def _extract_assertion_entities(assertion, soot_data_wrapped, dynamic_data):
    var_names = set()
    getter_candidates = set()
    comparisons = []
    parse_mode = "javalang"
    try:
        wrapped = f"public class Wrapper {{ public void test() {{ {assertion if assertion.strip().endswith(';') else assertion + ';'} }}}}"
        tokens = javalang.tokenizer.tokenize(wrapped)
        parser = javalang.parser.Parser(tokens)
        tree = parser.parse()

        for path, node in tree.filter(javalang.tree.MethodInvocation):
            q = getattr(node, 'qualifier', None)
            ident = _extract_id_from_expr(q)
            if ident:
                var_names.add(ident)
                after_data = dynamic_data.get('after', {}).get(ident, {})
                obj_getters = set(after_data.get('getters', {}).keys())
                for f_k, f_v in (after_data.get('fields', {}) or {}).items():
                    if isinstance(f_v, dict) and f_v.get('getter'):
                        obj_getters.add(f_v['getter'])
                    elif isinstance(f_v, str) and '__ACCESSIBLE:' in f_v:
                        obj_getters.add(f_v.split('__ACCESSIBLE:')[1].strip())
                if node.member not in obj_getters and not getattr(node, 'selectors', []):
                    if not hasattr(score_assertion_from_statement, "hallucinated_methods"):
                        score_assertion_from_statement.hallucinated_methods = set()
            else:
                getter_candidates.add(node.member)
            for arg in getattr(node, 'arguments', []) or []:
                if isinstance(arg, javalang.tree.MemberReference):
                    base = _extract_id_from_expr(getattr(arg, 'qualifier', None))
                    if base:
                        var_names.add(base)
                    elif getattr(arg, 'member', None):
                        var_names.add(arg.member)
                elif isinstance(arg, javalang.tree.MethodInvocation):
                    q2 = _extract_id_from_expr(getattr(arg, 'qualifier', None))
                    if q2:
                        var_names.add(q2)
                    else:
                        getter_candidates.add(arg.member)

        for path, node in tree.filter(javalang.tree.MemberReference):
            qual = getattr(node, 'qualifier', None)
            base = _extract_id_from_expr(qual)
            if base:
                var_names.add(base)
            elif getattr(node, 'member', None):
                var_names.add(node.member)

        try:
            for path, node in tree.filter(javalang.tree.BinaryOperation):
                op = getattr(node, 'operator', None)
                left = getattr(node, 'lhs', None) or getattr(node, 'left', None)
                right = getattr(node, 'rhs', None) or getattr(node, 'right', None)
                l_id = _extract_id_from_expr(left)
                r_val = None
                try:
                    if hasattr(right, 'value'):
                        r_val = str(right.value)
                    else:
                        r_val = _extract_id_from_expr(right)
                except Exception:
                    r_val = str(right)
                if op and (l_id or r_val):
                    comparisons.append((l_id, op, r_val))
        except Exception:
            try:
                for path, node in tree.filter(javalang.tree.BinaryExpression):
                    op = getattr(node, 'operator', None)
                    left = getattr(node, 'lhs', None) or getattr(node, 'left', None)
                    right = getattr(node, 'rhs', None) or getattr(node, 'right', None)
                    l_id = _extract_id_from_expr(left)
                    r_val = None
                    try:
                        if hasattr(right, 'value'):
                            r_val = str(right.value)
                        else:
                            r_val = _extract_id_from_expr(right)
                    except Exception:
                        r_val = str(right)
                    if op and (l_id or r_val):
                        comparisons.append((l_id, op, r_val))
            except Exception:
                pass
    except Exception:
        parse_mode = "regex_fallback"
        var_pattern = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b')
        java_keywords = set([
            'assertEquals', 'assertTrue', 'assertFalse', 'assertNull', 'assertNotNull',
            'assertSame', 'assertNotSame', 'assertThat', 'assertArrayEquals',
            'if', 'else', 'for', 'while', 'return', 'new', 'null', 'true', 'false',
            'int', 'long', 'float', 'double', 'boolean', 'char', 'byte', 'short', 'void',
            'public', 'private', 'protected', 'static', 'final', 'class', 'interface', 'enum',
            'this', 'super', 'package', 'import', 'extends', 'implements', 'throws', 'throw',
            'try', 'catch', 'finally', 'String', 'Object', 'Math', 'System', 'Exception', 'Throwable'
        ])
        assertion_clean = re.sub(r'".*?"|\'.*?\'|\b\d+(\.\d+[fFdD]?)?\b', '', assertion)
        candidates = set(var_pattern.findall(assertion_clean))
        if isinstance(soot_data_wrapped, dict) and "variables" in soot_data_wrapped:
            known_vars = set(v.get("variable") for v in soot_data_wrapped["variables"])
        else:
            known_vars = set(v.get("variable") for v in (soot_data_wrapped if isinstance(soot_data_wrapped, list) else []))
        known_vars.update(dynamic_data.get('diff', {}).keys())
        known_vars.update(dynamic_data.get('after', {}).keys())
        known_vars = {kv for kv in known_vars if not (isinstance(kv, str) and kv.strip().lower().startswith('(implicit'))}
        for v in candidates:
            if v not in java_keywords and v in known_vars:
                var_names.add(v)
        dotted_tokens = re.findall(r'([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)', assertion)
        for tok in dotted_tokens:
            base = tok.split('.')[0]
            if base in known_vars:
                var_names.add(base)
        simple_tokens = re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(', assertion)
        for tok in simple_tokens:
            if tok in known_vars:
                var_names.add(tok)
            if '.' in tok:
                pref = tok.split('.')[0]
                if pref in known_vars:
                    var_names.add(pref)

    if getter_candidates and not var_names:
        after = dynamic_data.get('after', {})
        for g in getter_candidates:
            for var, st in after.items():
                getters_map = st.get('getters', {}) or {}
                if g in getters_map:
                    var_names.add(var)
                    break
                fields = st.get('fields', {}) or {}
                for f_k, f_v in fields.items():
                    if isinstance(f_v, dict):
                        if f_v.get('getter') == g or g in str(f_v.get('getter', '')):
                            var_names.add(var)
                            break
                    elif isinstance(f_v, str) and ('__ACCESSIBLE:' + g) in f_v:
                        var_names.add(var)
                        break
                if var in var_names:
                    break

    raw_var_names = list(var_names)
    filtered_var_names = [
        v for v in raw_var_names
        if not (isinstance(v, str) and v.strip().lower().startswith('(implicit'))
    ]

    extraction_debug = {
        "parse_mode": parse_mode,
        "raw_var_names": sorted(str(v) for v in raw_var_names if isinstance(v, str)),
        "filtered_var_names": sorted(str(v) for v in filtered_var_names if isinstance(v, str)),
        "getter_candidates": sorted(str(v) for v in getter_candidates if isinstance(v, str)),
        "comparison_count": len(comparisons),
    }
    return filtered_var_names, comparisons, extraction_debug


def _diagnose_variable_matching(var_names, var_scores, soot_data_wrapped, dynamic_data, extraction_debug=None):
    extraction_debug = extraction_debug or {}
    var_score_keys = set(var_scores.keys()) if isinstance(var_scores, dict) else set()
    soot_vars = []
    if isinstance(soot_data_wrapped, dict) and isinstance(soot_data_wrapped.get("variables"), list):
        soot_vars = soot_data_wrapped.get("variables", [])
    elif isinstance(soot_data_wrapped, list):
        soot_vars = soot_data_wrapped

    soot_var_names = []
    implicit_present = False
    for item in soot_vars:
        if not isinstance(item, dict):
            continue
        name = item.get("variable")
        if isinstance(name, str):
            soot_var_names.append(name)
            if name.strip().lower().startswith("(implicit"):
                implicit_present = True

    dynamic_after = dynamic_data.get("after", {}) if isinstance(dynamic_data, dict) else {}
    dynamic_before = dynamic_data.get("before", {}) if isinstance(dynamic_data, dict) else {}
    dynamic_diff = dynamic_data.get("diff", {}) if isinstance(dynamic_data, dict) else {}
    dynamic_var_names = set()
    if isinstance(dynamic_after, dict):
        dynamic_var_names.update(k for k in dynamic_after if isinstance(k, str))
    if isinstance(dynamic_before, dict):
        dynamic_var_names.update(k for k in dynamic_before if isinstance(k, str))
    if isinstance(dynamic_diff, dict):
        dynamic_var_names.update(k for k in dynamic_diff if isinstance(k, str))

    requested = [v for v in var_names if isinstance(v, str)]
    matched = [v for v in requested if v in var_score_keys]
    missing = [v for v in requested if v not in var_score_keys]

    reason_codes = []
    if not requested:
        reason_codes.append("no_var_extracted")
    if requested and not matched:
        reason_codes.append("extracted_but_unmatched")
    if not soot_var_names:
        reason_codes.append("no_soot_variables")
    if implicit_present:
        reason_codes.append("implicit_unknown_present")
    if not dynamic_var_names:
        reason_codes.append("no_dynamic_variables")
    getter_candidates = extraction_debug.get("getter_candidates", []) or []
    if getter_candidates and not requested:
        reason_codes.append("getter_only_no_receiver_match")
    if missing and dynamic_var_names:
        missing_not_in_dynamic = [v for v in missing if v not in dynamic_var_names]
        if len(missing_not_in_dynamic) == len(missing):
            reason_codes.append("missing_vars_not_in_dynamic")
    if missing and soot_var_names:
        soot_var_set = set(soot_var_names)
        missing_not_in_soot = [v for v in missing if v not in soot_var_set]
        if len(missing_not_in_soot) == len(missing):
            reason_codes.append("missing_vars_not_in_soot")
    if requested and not var_score_keys:
        reason_codes.append("empty_var_scores")

    return {
        "requested_var_names": requested,
        "matched_var_names": matched,
        "missing_var_names": missing,
        "var_score_keys": sorted(str(v) for v in var_score_keys if isinstance(v, str)),
        "soot_var_names": sorted(str(v) for v in soot_var_names),
        "dynamic_var_names": sorted(str(v) for v in dynamic_var_names),
        "reason_codes": reason_codes,
        "implicit_unknown_present": implicit_present,
        "extraction_debug": extraction_debug,
    }


def _aggregate_assertion_variable_scores(assertion, soot_data, soot_data_wrapped, var_names, comparisons, var_scores):
    assertion_scores = {}
    total_score = 0.0
    logic_alignment_bonus = 0.0
    constraint_alignment_hits = 0
    has_any_constraints = False
    assertion_lower = assertion.lower()
    for v in var_names:
        if v not in var_scores:
            continue
        v_data = var_scores[v]
        assertion_scores[v] = v_data
        total_score += v_data['total_score']
        soot_var_info = next((sv for sv in (soot_data if isinstance(soot_data, list) else []) if sv.get("variable") == v), {})
        if not soot_var_info and isinstance(soot_data_wrapped, dict):
            soot_var_info = next((sv for sv in soot_data_wrapped.get("variables", []) if sv.get("variable") == v), {})
        cfi = soot_var_info.get("control_flow_influence", {}) if isinstance(soot_var_info, dict) else {}
        if not isinstance(cfi, dict):
            cfi = {}
        constraints = cfi.get("path_constraints", [])
        if not isinstance(constraints, list):
            constraints = []
        if constraints:
            has_any_constraints = True
        applied_bonuses = set()
        for cond in constraints:
            cond_lower = cond.lower()
            constants = re.findall(r'\b(null|true|false|0(\.0f)?)\b', cond_lower)
            for const_tuple in constants:
                const = const_tuple[0]
                if const in assertion_lower and f"const_{const}" not in applied_bonuses:
                    logic_alignment_bonus += weights.get("logic_const_match_bonus", 8.0)
                    constraint_alignment_hits += 1
                    applied_bonuses.add(f"const_{const}")
            if any(op in cond_lower for op in ["size", "length", "empty", "0"]):
                if any(target in assertion_lower for target in ["size", "length", "isempty", "hasmore", "next"]) and "size_empty" not in applied_bonuses:
                    logic_alignment_bonus += weights.get("logic_size_empty_bonus", 10.0)
                    constraint_alignment_hits += 1
                    applied_bonuses.add("size_empty")
            for (l_id, operator, r_val) in comparisons:
                if not l_id:
                    continue
                if l_id == v or l_id in cond_lower or l_id in assertion_lower:
                    if any(t in cond_lower for t in [operator]) or str(r_val) in cond_lower:
                        key = f"comp_{l_id}_{operator}_{r_val}"
                        if key not in applied_bonuses:
                            logic_alignment_bonus += weights.get("logic_comp_bonus", 8.0)
                            constraint_alignment_hits += 1
                            applied_bonuses.add(key)
            if "throw" in cond_lower or "null" in cond_lower:
                if any(target in assertion_lower for target in ["assertnotnull", "assertsame", "notnull"]) and "not_null" not in applied_bonuses:
                    logic_alignment_bonus += weights.get("logic_exception_bonus", 6.0)
                    constraint_alignment_hits += 1
                    applied_bonuses.add("not_null")
        v_role = v_data.get("role") if isinstance(v_data, dict) else None
        if not v_role:
            v_role = soot_var_info.get("role", "") if isinstance(soot_var_info, dict) else ""
        if "Return Value" in (v_role or "") and "ret_bonus" not in applied_bonuses:
            if any(c_word in assertion_lower for c_word in ["result", "val", "count", "idx", "asserttrue", "assertfalse", "assertequals"]):
                logic_alignment_bonus += weights.get("logic_return_bonus", 4.0)
                applied_bonuses.add("ret_bonus")

    logic_cap = weights.get("logic_cap", 20.0)
    logic_alpha = weights.get("logic_alpha", 0.25)
    prop_cap = logic_alpha * max(total_score, 1.0)
    logic_alignment_bonus = min(logic_alignment_bonus, prop_cap, logic_cap)
    total_score += logic_alignment_bonus
    return {
        "assertion_scores": assertion_scores,
        "total_score": total_score,
        "logic_alignment_bonus": logic_alignment_bonus,
        "constraint_alignment_hits": constraint_alignment_hits,
        "has_any_constraints": has_any_constraints,
        "logic_cap": logic_cap,
    }


def _is_low_signal_assertion(assertion_scores):
    if not assertion_scores:
        return True
    for vd in assertion_scores.values():
        bd = vd.get('breakdown', {}) if isinstance(vd, dict) else {}
        if bd.get('Complexity', 0) > 0 or bd.get('Impact', 0) > 0 or bd.get('Modification', 0) > 0 or bd.get('Role', 0) > 0:
            return False
    return True


def _apply_trivial_assertion_penalty(assertion, total_score, assertion_scores):
    mult = float(weights.get("trivial_assertion_multiplier", 0.1))
    if _is_low_signal_assertion(assertion_scores):
        if re.search(r'(?:[A-Za-z0-9_$.]+\.)*assert(?:Not)?Null\s*\(', assertion, re.IGNORECASE):
            total_score *= mult
        elif re.search(r'(?:[A-Za-z0-9_$.]+\.)*assert(?:True|False)\s*\(\s*[A-Za-z0-9_$.]+\s*(?:==|!=)\s*null\s*\)', assertion, re.IGNORECASE):
            total_score *= mult
    return total_score


def _clamp_label_bonus(label_bonus, logic_cap):
    clamp_frac = float(weights.get('label_bonus_clamp_frac', 0.2))
    max_abs = float(logic_cap) * clamp_frac
    if label_bonus > max_abs:
        return max_abs
    if label_bonus < -max_abs:
        return -max_abs
    return label_bonus


def _build_fallback_assertion_result(assertion, var_names, comparisons, assertion_label):
    sym = symbolize_and_vectorize_assertion(assertion)
    hist = sym.get('hist', {})
    token_score = 0.0
    token_score += hist.get('size', 0) * 2.0
    token_score += hist.get('length', 0) * 1.5
    token_score += hist.get('isEmpty', 0) * 2.0
    token_score += hist.get('get', 0) * 0.8
    token_score += hist.get('contains', 0) * 1.5
    token_score += hist.get('equals', 0) * 1.0
    token_score += hist.get('not', 0) * 0.5
    token_score += hist.get('null', 0) * 1.2
    token_score += hist.get('true', 0) * 0.8
    token_score += hist.get('false', 0) * 0.8
    if re.search(r"\b\d+\b", assertion):
        token_score += 1.5
    if re.search(r'assertEquals|assertTrue|assertFalse|assertNotNull|assertNotEquals', assertion):
        token_score += 1.0
    total_score = float(token_score) + 1.0
    assertion_semantic_subtype = infer_assertion_semantic_subtype(
        assertion, assertion_label, {}, comparisons=comparisons,
        has_any_constraints=False, constraint_alignment_hits=0,
    )
    label_bonus = compute_contextual_label_bonus(
        assertion_label, assertion_semantic_subtype, {},
        has_any_constraints=False, constraint_alignment_hits=0, comparisons=comparisons,
    )
    label_bonus = _clamp_label_bonus(label_bonus, float(weights.get('logic_cap', 20.0)))
    total_score += label_bonus
    contract_sensitivity_bonus = compute_contract_sensitivity_bonus(
        assertion, assertion_label, assertion_semantic_subtype, {}, comparisons=comparisons
    )
    total_score += contract_sensitivity_bonus
    logic_cap = float(weights.get('logic_cap', 20.0))
    k_sig = float(weights.get('logistic_k', 1.0))
    mid = 0.5 * logic_cap
    scale = max(1.0, logic_cap / 4.0)
    z = (total_score - mid) / scale
    normalized_total = 1.0 / (1.0 + math.exp(-k_sig * z))
    semantic_payload = _compute_semantic_outputs(assertion, {}, {}, total_score) if SEMANTIC_REWARD_ENABLED else {
        "semantic_score": None,
        "semantic_confidence": None,
        "semantic_raw": None,
        "total_score_with_semantic": total_score,
        "semantic_sigmoid": None,
    }
    normalized_total_with_semantic = _fuse_normalized_with_semantic(
        normalized_total,
        semantic_payload.get("semantic_score"),
        semantic_payload.get("semantic_confidence"),
        semantic_payload.get("semantic_sigmoid"),
    )
    training_reward = _compute_training_reward({}, total_score, normalized_total_with_semantic)
    return {
        'assertion': assertion,
        'variables': var_names,
        'matched_scores': {},
        'total_score': total_score,
        'semantic_score': semantic_payload.get('semantic_score'),
        'semantic_confidence': semantic_payload.get('semantic_confidence'),
        'semantic_raw': semantic_payload.get('semantic_raw'),
        'total_score_with_semantic': semantic_payload.get('total_score_with_semantic', total_score),
        'logic_alignment_bonus': 0,
        'normalized_total': normalized_total,
        'training_reward': training_reward,
        'normalized_total_with_semantic': normalized_total_with_semantic,
        'semantic_sigmoid': semantic_payload.get('semantic_sigmoid'),
        'assertion_label': assertion_label,
        'assertion_semantic_subtype': assertion_semantic_subtype,
        'label_bonus': label_bonus,
        'contract_sensitivity_bonus': contract_sensitivity_bonus,
        'match_debug': {},
        'note': 'Fallback token-histogram scoring used (no variables matched).'
    }


def _compute_semantic_outputs(assertion, soot_data_wrapped, dynamic_data, total_score):
    semantic_score = None
    semantic_conf = None
    semantic_raw = None
    total_with_sem = total_score
    sem_sig = None
    try:
        sample_ctx = {
            'soot_analysis_result': soot_data_wrapped,
            'dynamic_analysis': dynamic_data
        }
        model_res = assess_assertion_via_ir(assertion, sample_ctx)
        if isinstance(model_res, dict):
            semantic_score = model_res.get('score') or (model_res.get('parsed') or {}).get('score')
            semantic_conf = (model_res.get('parsed') or {}).get('confidence') if model_res.get('parsed') else None
            semantic_raw = model_res.get('raw_response')
        elif isinstance(model_res, (int, float)):
            semantic_score = float(model_res)
    except Exception:
        semantic_score = None
        semantic_conf = None
        semantic_raw = None

    try:
        if semantic_score is not None:
            sem_center = float(weights.get('semantic_center', 50.0))
            sem_scale = float(weights.get('semantic_scale', 0.15))
            k_sig = float(weights.get('logistic_k', 1.0))
            logic_cap = float(weights.get('logic_cap', 20.0))
            mapping = weights.get('semantic_mapping', 'sigmoid')
            if mapping == 'linear':
                sem_contrib = (float(semantic_score) - sem_center) * sem_scale
                total_with_sem = total_score + sem_contrib
                sem_sig = 1.0 / (1.0 + math.exp(-k_sig * ((float(semantic_score) - sem_center) / max(1e-8, logic_cap))))
            else:
                denom = max(1e-8, sem_scale * logic_cap)
                sem_z = (float(semantic_score) - sem_center) / denom
                sem_sig = 1.0 / (1.0 + math.exp(-k_sig * sem_z))
                total_with_sem = total_score + (sem_sig - 0.5) * logic_cap
    except Exception:
        total_with_sem = total_score
        sem_sig = None

    return {
        "semantic_score": semantic_score,
        "semantic_confidence": semantic_conf,
        "semantic_raw": semantic_raw,
        "total_score_with_semantic": total_with_sem,
        "semantic_sigmoid": sem_sig,
    }


def _compute_assertion_normalized_total(assertion_scores, total_score):
    """Normalize at assertion level first, then use variable-normalized signal as a small prior."""
    n_vars = max(1, len(assertion_scores))
    heuristic_cap = max(1.0, weights.get('logic_cap', 20.0) * n_vars)
    mid = 0.5 * heuristic_cap
    scale = max(1.0, heuristic_cap / 5.0)
    k_sig = float(weights.get('logistic_k', 1.0))
    z = (total_score - mid) / scale
    base_norm = 1.0 / (1.0 + math.exp(-k_sig * z))

    norm_vals = [vd.get('normalized_total') for vd in assertion_scores.values() if vd.get('normalized_total') is not None]
    if not norm_vals:
        return base_norm

    var_signal = float(sum(norm_vals)) / len(norm_vals)
    # Variable-level normalized scores remain useful as a weak prior about the
    # importance of the matched variables, but assertion-level total_score must dominate.
    mixed = 0.85 * base_norm + 0.15 * var_signal
    return max(0.0, min(1.0, mixed))


def _fuse_normalized_with_semantic(normalized_total, semantic_score, semantic_conf, semantic_sigmoid):
    if semantic_score is None:
        return normalized_total
    try:
        w_h = float(weights.get('fusion_w_heuristic', 0.8))
        w_s = float(weights.get('fusion_w_semantic', 0.2))
        if semantic_conf is not None:
            try:
                conf = float(semantic_conf)
                w_s = w_s * max(0.0, min(1.0, conf))
            except Exception:
                pass
        total_w = w_h + w_s
        if total_w <= 0:
            return normalized_total
        w_h_norm = w_h / total_w
        w_s_norm = w_s / total_w
        semantic_component = semantic_sigmoid if semantic_sigmoid is not None else 0.5
        fused = w_h_norm * normalized_total + w_s_norm * semantic_component
        return max(0.0, min(1.0, fused))
    except Exception:
        return normalized_total


def _compute_training_reward(assertion_scores, total_score, normalized_total):
    """
    Training-time reward mapping.

    `normalized_total` is good for evaluation summaries, but it compresses too
    many assertions into the 0.95~1.0 band. For RL we keep the same ordering
    direction and mix in a cap-relative linear ratio so high-score assertions
    remain distinguishable.
    """
    n_vars = max(1, len(assertion_scores))
    heuristic_cap = max(1.0, float(weights.get('logic_cap', 20.0)) * n_vars)
    cap_multiplier = max(1.0, float(weights.get('train_reward_cap_multiplier', 1.25)))
    effective_cap = heuristic_cap * cap_multiplier

    ratio_component = max(0.0, min(1.0, float(total_score) / max(1.0, effective_cap)))

    norm_vals = [
        vd.get('normalized_total')
        for vd in assertion_scores.values()
        if vd.get('normalized_total') is not None
    ]
    if norm_vals:
        var_component = max(0.0, min(1.0, float(sum(norm_vals)) / len(norm_vals)))
    else:
        var_component = max(0.0, min(1.0, float(normalized_total)))

    w_ratio = float(weights.get('train_reward_ratio_weight', 0.70))
    w_sig = float(weights.get('train_reward_sigmoid_weight', 0.20))
    w_var = float(weights.get('train_reward_var_weight', 0.10))
    total_w = max(1e-8, w_ratio + w_sig + w_var)

    mixed = (
        w_ratio * ratio_component +
        w_sig * max(0.0, min(1.0, float(normalized_total))) +
        w_var * var_component
    ) / total_w
    return max(0.0, min(1.0, mixed))


def score_assertion_from_statement(assertion: str, soot_data_wrapped, dynamic_data):
    """
    Given an assertion statement (Java), soot_data, and dynamic_data,
    extract the variables involved in the assertion, score them using quantify_assertion_value,
    and return a summary of the assertion's score.
    """
    if isinstance(soot_data_wrapped, dict) and "variables" in soot_data_wrapped:
        soot_data = soot_data_wrapped["variables"]
    else:
        soot_data = soot_data_wrapped
    var_names, comparisons, extraction_debug = _extract_assertion_entities(assertion, soot_data_wrapped, dynamic_data)
    assertion_label = extract_assertion_label(assertion, var_names, comparisons)
    var_scores = quantify_assertion_value(soot_data_wrapped, dynamic_data)
    match_debug = _diagnose_variable_matching(
        var_names,
        var_scores,
        soot_data_wrapped,
        dynamic_data,
        extraction_debug=extraction_debug,
    )
    aggregate = _aggregate_assertion_variable_scores(
        assertion, soot_data, soot_data_wrapped, var_names, comparisons, var_scores
    )
    assertion_scores = aggregate["assertion_scores"]
    total_score = aggregate["total_score"]
    logic_alignment_bonus = aggregate["logic_alignment_bonus"]
    constraint_alignment_hits = aggregate["constraint_alignment_hits"]
    has_any_constraints = aggregate["has_any_constraints"]
    logic_cap = aggregate["logic_cap"]
    assertion_semantic_subtype = infer_assertion_semantic_subtype(
        assertion,
        assertion_label,
        assertion_scores,
        comparisons=comparisons,
        has_any_constraints=has_any_constraints,
        constraint_alignment_hits=constraint_alignment_hits,
    )
    total_score = _apply_trivial_assertion_penalty(assertion, total_score, assertion_scores)
    label_bonus = compute_contextual_label_bonus(
        assertion_label,
        assertion_semantic_subtype,
        assertion_scores,
        has_any_constraints=has_any_constraints,
        constraint_alignment_hits=constraint_alignment_hits,
        comparisons=comparisons,
    )
    label_bonus = _clamp_label_bonus(label_bonus, logic_cap)
    total_score += label_bonus
    contract_sensitivity_bonus = compute_contract_sensitivity_bonus(
        assertion,
        assertion_label,
        assertion_semantic_subtype,
        assertion_scores,
        comparisons=comparisons,
    )
    total_score += contract_sensitivity_bonus
    if hasattr(score_assertion_from_statement, "hallucinated_methods") and score_assertion_from_statement.hallucinated_methods:
        match_debug["hallucinated_methods"] = sorted(
            str(v) for v in score_assertion_from_statement.hallucinated_methods
        )
        score_assertion_from_statement.hallucinated_methods.clear()
    else:
        match_debug["hallucinated_methods"] = []
    if not assertion_scores:
        fallback = _build_fallback_assertion_result(assertion, var_names, comparisons, assertion_label)
        fallback["match_debug"] = match_debug
        return fallback

    normalized_total = _compute_assertion_normalized_total(assertion_scores, total_score)
    semantic_payload = _compute_semantic_outputs(assertion, soot_data_wrapped, dynamic_data, total_score) if SEMANTIC_REWARD_ENABLED else {
        "semantic_score": None,
        "semantic_confidence": None,
        "semantic_raw": None,
        "total_score_with_semantic": total_score,
        "semantic_sigmoid": None,
    }
    normalized_total_with_semantic = _fuse_normalized_with_semantic(
        normalized_total,
        semantic_payload.get("semantic_score"),
        semantic_payload.get("semantic_confidence"),
        semantic_payload.get("semantic_sigmoid"),
    )
    training_reward = _compute_training_reward(assertion_scores, total_score, normalized_total_with_semantic)
    return {
        'assertion': assertion,
        'variables': var_names,
        'matched_scores': assertion_scores,
        'logic_alignment_bonus': logic_alignment_bonus,
        'total_score': total_score,
        'semantic_score': semantic_payload.get('semantic_score'),
        'semantic_confidence': semantic_payload.get('semantic_confidence'),
        'semantic_raw': semantic_payload.get('semantic_raw'),
        'total_score_with_semantic': semantic_payload.get('total_score_with_semantic', total_score),
        'assertion_label': assertion_label,
        'assertion_semantic_subtype': assertion_semantic_subtype,
        'label_bonus': label_bonus,
        'contract_sensitivity_bonus': contract_sensitivity_bonus,
        'match_debug': match_debug,
        'normalized_total': normalized_total,
        'training_reward': training_reward,
        'normalized_total_with_semantic': normalized_total_with_semantic
    }

def create_client():
    return OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com",
        timeout=60.0
    )

def model_judge(prompt: str, model="deepseek-v4-flash", temperature=0.0, timeout=60.0):
    """Call the DeepSeek chat model with `prompt` and try to return a parsed JSON/dict or numeric score.
    Returns: dict | float | str (raw)"""
    client = create_client()
    messages = [{"role": "user", "content": prompt}]
    try:
        # DeepSeek v4 defaults to thinking mode; force non-thinking mode for stable cost/runtime.
        request_kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        try:
            response = client.chat.completions.create(**request_kwargs)
        except TypeError:
            # Fallback for SDK variants that don't accept extra_body.
            request_kwargs.pop("extra_body", None)
            response = client.chat.completions.create(**request_kwargs)
        full_response = ""
        for chunk in response:
            # streaming deltas may not have content
            content = getattr(chunk.choices[0].delta, 'content', None)
            if content is not None:
                full_response += content

        full_response = full_response.strip()

        # try extract first JSON object
        m = re.search(r'(\{[\s\S]*\})', full_response)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass

        # try parse float
        try:
            return float(full_response.strip())
        except Exception:
            return full_response
    finally:
        try:
            client.close()
        except Exception:
            pass

def symbolize_and_vectorize_assertion(assertion: str) -> dict:
    """Produce a symbolized form and a lightweight vector-like token histogram.

    - `symbolized`: replace identifiers with generic placeholders (V1, V2)
    - `tokens`: token list
    - `hist`: token frequency dict
    This is intentionally simple and deterministic (no ML embeddings required).
    """
    a = assertion.strip()
    # extract identifiers (very simple heuristic: words with dot or camelCase)
    ids = re.findall(r'([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)', a)
    # sort by length (longer first) to avoid partial replacements
    unique_ids = []
    for i in ids:
        if i not in unique_ids and not re.fullmatch(r'assert(True|False|Equals|NotNull|Null)', i):
            unique_ids.append(i)

    symbol_map = {}
    for idx, name in enumerate(unique_ids, start=1):
        symbol_map[name] = f'V{idx}'

    symbolized = a
    for name, sym in sorted(symbol_map.items(), key=lambda x: -len(x[0])):
        # word-bound replacement
        symbolized = re.sub(r'\b' + re.escape(name) + r'\b', sym, symbolized)

    # tokenize: operators and words
    tokens = re.findall(r'\w+|==|!=|<=|>=|\(|\)|\.|\[|\]|>|<|\+|-|\*|/', symbolized)
    hist = {}
    for t in tokens:
        hist[t] = hist.get(t, 0) + 1

    return {'symbolized': symbolized, 'tokens': tokens, 'hist': hist, 'symbol_map': symbol_map}

def assess_assertion_via_ir(assertion: str, sample: dict, model: str = "deepseek-v4-flash", temperature: float = 0.0, timeout: float = 60.0):
    """Full pipeline: SPEC -> IR, assertion -> symbolism/vector, then ask model to judge relevance

    Returns combined dict with IR, symbolized assertion, and model judgement.
    """
    # Prefer a symbolized spec-IR if soot analysis produced one; otherwise extract comments and generate IR
    raw = ''
    if isinstance(sample, dict):
        raw = sample.get('raw_method') or sample.get('method') or sample.get('source') or ''
    raw = raw or ''

    # try to pick up spec_ir_symbolized produced by Soot analysis
    spec_ir = None
    spec_ir_symbolized = None
    try:
        soot_res = sample.get('soot_analysis_result') if isinstance(sample, dict) else None
        if isinstance(soot_res, dict):
            ir_block = soot_res.get('ir', {}) or {}
            spec_ir = ir_block.get('spec_ir')
            spec_ir_symbolized = ir_block.get('spec_ir_symbolized')
    except Exception:
        spec_ir = None
        spec_ir_symbolized = None

    # If symbolized IR exists (preferred), use it directly as `ir` for prompting
    if spec_ir_symbolized and isinstance(spec_ir_symbolized, dict):
        ir = spec_ir_symbolized
        spec_symbol_map = ir.get('symbol_map', {})
        ir_source = 'soot_spec_symbolized'
    else:
        # Do NOT generate spec IR from comments here — prefer only Soot-provided symbolized IR.
        # Use a minimal empty IR placeholder to keep prompt shape consistent.
        ir = {'preconditions': [], 'postconditions': [], 'observations': [], 'pseudo_code': '(no spec_ir_symbolized available)'}
        spec_symbol_map = {}
        ir_source = 'none_available'

    sym = symbolize_and_vectorize_assertion(assertion)

    # Build prompt that includes the IR pseudo_code (symbolized preferably), the symbolized assertion,
    # and the spec->symbol mapping to allow the model to reason about placeholders.
    prompt = (
        "You are an expert verifier.\n"
        "Below is a small verifiable IR extracted from a method SPEC (symbolized when available), and a symbolized ASSERTION.\n"
        "Judge the ASSERTION against the SPEC using the following FIVE independent dimensions. For each dimension return an integer score 0-100. Also return a numeric `confidence` (0.0-1.0).Do NOT provide any reasoning, explanation.\n\n"
        "1) core_postcondition (20 points): how well the assertion covers the SPEC's core postcondition(s).\n"
        "2) public_contract (20 points): whether the assertion respects the public contract (does not depend on private/internal fields).\n"
        "3) exposes_risk (20 points): whether the assertion is likely to expose high-risk defects (branches, exceptions, state changes).\n"
        "4) maintenance_cost (20 points): estimated maintenance cost; lower reliance on implementation details -> higher score.\n"
        "5) completeness (20 points): how completely the assertion covers the variable(s) core complexity.\n\n"
        "Return ONLY JSON with these keys: {\"core_postcondition\":int, \"public_contract\":int, \"exposes_risk\":int, \"maintenance_cost\":int, \"completeness\":int, \"confidence\":float}.\n\n"
        f"IR_SOURCE: {ir_source}\n"
        "IR_PSEUDO_CODE:\n" + (ir.get('pseudo_code') or '(none)') + "\n\n"
        "SYMBOLIZED_ASSERTION:\n" + sym['symbolized'] + "\n\n"
        "SPEC_SYMBOL_MAP:\n" + (json.dumps(spec_symbol_map) if spec_symbol_map else '(none)') + "\n\n"
        "Return compact JSON only."
    )

    raw_response = model_judge(prompt, model=model, temperature=temperature, timeout=timeout)

    # parse similar to existing helpers
    parsed = None
    if isinstance(raw_response, dict):
        parsed = raw_response
    else:
        try:
            parsed = json.loads(raw_response)
        except Exception:
            m = re.search(r'(\{[\s\S]*?\})', str(raw_response))
            if m:
                try:
                    parsed = json.loads(m.group(1))
                except Exception:
                    parsed = None

    # If model returned the five-dimension scores, compute a single aggregated score (average)
    if isinstance(parsed, dict):
        dims = ['core_postcondition', 'public_contract', 'exposes_risk', 'maintenance_cost', 'completeness']
        dim_vals = []
        for d in dims:
            try:
                v = parsed.get(d)
                if v is None:
                    raise ValueError
                # coerce to float/int
                dim_vals.append(float(v))
            except Exception:
                # missing or invalid -> skip aggregation and leave parsed as-is
                dim_vals = []
                break
        if dim_vals:
            avg_score = float(sum(dim_vals) / len(dim_vals))
            parsed['score'] = avg_score

    out = {
        'ir': ir,
        'spec_ir': spec_ir,
        'spec_ir_symbolized': spec_ir_symbolized,
        'symbolized': sym,
        'raw_response': raw_response,
        'parsed': parsed
    }
    if isinstance(parsed, dict):
        out.update({
            'score': parsed.get('score'),
            'confidence': parsed.get('confidence'),
            # expose the detailed per-dimension fields if provided
            'core_postcondition': parsed.get('core_postcondition'),
            'public_contract': parsed.get('public_contract'),
            'exposes_risk': parsed.get('exposes_risk'),
            'maintenance_cost': parsed.get('maintenance_cost'),
            'completeness': parsed.get('completeness')
        })
    return out
