import json
import numpy as np
import scipy.stats as stats
import itertools
import os
import javalang

def get_recommendation(var_name, scores, role, soot_var=None, dynamic_diff=None):
    
    recs = []
    if "Return Value" in role:
        recs.append("MUST: Assert return value (assertEquals/True/False).")
    
    if scores["Modification"] > 5 and "Return Value" not in role:
        state_access = (soot_var or {}).get("data_flow_state_access", {})
        static_writes = state_access.get("static_fields_modified", 0)

        field_changes = (dynamic_diff or {}).get("field_changes", {}) if dynamic_diff else {}
        deep_changes = (dynamic_diff or {}).get("deep_changes", {}) if dynamic_diff else {}
        element_changes = (dynamic_diff or {}).get("element_changes", {}) if dynamic_diff else {}
        value_change = (dynamic_diff or {}).get("value_change") if dynamic_diff else None
        array_length_change = (dynamic_diff or {}).get("array_length_change") if dynamic_diff else None
        collection_size_change = (dynamic_diff or {}).get("collection_size_change") if dynamic_diff else None
        has_any_change = bool(field_changes or deep_changes or element_changes or value_change or array_length_change or collection_size_change)

        accessible_changes = [f for f, c in field_changes.items() if c.get("getter")]
        inaccessible_changes = [f for f, c in field_changes.items() if not c.get("getter")]

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
weights = {
    "role_return_no_side_effects": 28,
    "role_return_with_side_effects": 22,
    "role_focal": 14,
    "role_input": 6,
    "role_other": 1,
    "mod_new": 30,
    "mod_accessible": 28,
    "mod_field": 18,
    "mod_none": 0,
    "impact_branch_weight": 1.2,
    "impact_cap": 15,
    "impact_nonmodified_multiplier": 0.25,
    "complexity_fields": 2.5,
    "complexity_elements": 3.5,
    "complexity_deep_fields": 6,
    "complexity_static_fields": 2.5,
    "bonus_return_no_side_effects": 6,
    "penalty_focal_return_unmodified": -3
}
def quantify_assertion_value(soot_data_wrapped, dynamic_data):
    results = {}
    if isinstance(soot_data_wrapped, dict) and "error" in soot_data_wrapped:
        return {"error": soot_data_wrapped["error"]}
    if isinstance(dynamic_data, dict) and "error" in dynamic_data:
        return {"error": dynamic_data["error"]}

    if isinstance(soot_data_wrapped, dict) and "variables" in soot_data_wrapped:
        soot_data = soot_data_wrapped["variables"]
        global_info = soot_data_wrapped.get("global_metrics", {})
    else:
        soot_data = soot_data_wrapped if isinstance(soot_data_wrapped, list) else []
        global_info = {}

    # Check for global side effects (any variable modified)
    has_side_effects = len(dynamic_data.get("diff", {})) > 0
    # Build a unified variable map from both Soot and Dynamic Analysis
    soot_vars_map = {v["variable"]: v for v in soot_data}
    dynamic_diff_vars = dynamic_data.get("diff", {})
    all_varnames = set(soot_vars_map.keys()) | set(dynamic_diff_vars.keys())

    # --- New Logic: Return Value Inheritance (Optimization 1) ---
    # If a variable is a Return Value, it should inherit path_constraints from the Focal Object (THIS)
    # because the return value's existence/state often depends on the Focal Object's logic.
    focal_vars = [v for v, info in soot_vars_map.items() if "Focal Object" in info.get("role", "")]
    if focal_vars:
        focal_constraints = []
        for fv in focal_vars:
            focal_constraints.extend(soot_vars_map[fv].get("control_flow_influence", {}).get("path_constraints", []))
        
        for var_name in all_varnames:
            info = soot_vars_map.get(var_name, {})
            if "Return Value" in info.get("role", ""):
                if "control_flow_influence" not in info:
                    info["control_flow_influence"] = {}
                existing = info["control_flow_influence"].get("path_constraints", [])
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
        if var_name in dynamic_data.get("diff", {}):
            diff_info = dynamic_data["diff"][var_name]
            if diff_info.get("type") == "new":
                score_breakdown["Modification"] = weights["mod_new"]
            else:
                # Accessible modification (getter/array/collection/element)
                field_changes = diff_info.get("field_changes", {})
                accessible = any(c.get("getter") for c in field_changes.values())
                if accessible or diff_info.get("collection_size_change") or diff_info.get("array_length_change") or len(diff_info.get("element_changes", {})) > 0:
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
        branches = cf_influence.get("branch_decisions_dependent_on_state", 0) + cf_influence.get("branch_decisions", 0)
        # Extra weight for specific usage in branch conditions (Logic Constraint)
        branch_cond_bonus = cf_influence.get("branch_condition_usage", 0) * 0.5 
        # PDG Influence: credit based on number of dependent nodes in the PDG
        pdg_bonus = min(cf_influence.get("pdg_dependants", 0) * 0.2, 5.0) # Cap PDG bonus
        
        exception_paths = cf_influence.get("exception_paths", 0)
        flow_influence = soot_var.get("flow_influence", {})
        branches_init = flow_influence.get("initializes_state_used_in_branches", 0)
        total_branches = branches + branches_init + (exception_paths * 1) + branch_cond_bonus + pdg_bonus
        raw_impact = min(total_branches * weights["impact_branch_weight"], weights["impact_cap"])
        if is_modified or ("Return Value" in role):
            score_breakdown["Impact"] = raw_impact
        else:
            score_breakdown["Impact"] = raw_impact * weights["impact_nonmodified_multiplier"]

        # 4. Complexity
        after_state = dynamic_data.get("after", {}).get(var_name, {})
        fields = after_state.get("fields", {})
        deep_fields = after_state.get("deep_fields", {})
        elements = after_state.get("elements", {})
        static_fields_list = soot_var.get("data_flow_state_access", {}).get("static_fields_list", [])
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
            "recommendation": get_recommendation(
                var_name,
                score_breakdown,
                role,
                soot_var=soot_var,
                dynamic_diff=dynamic_data.get("diff", {}).get(var_name)
            )
        }

    sorted_results = dict(sorted(results.items(), key=lambda item: item[1]['total_score'], reverse=True))
    return sorted_results

def score_assertion_from_statement(assertion: str, soot_data_wrapped, dynamic_data):
    """
    Given an assertion statement (Java), soot_data, and dynamic_data,
    extract the variables involved in the assertion, score them using quantify_assertion_value,
    and return a summary of the assertion's score.
    """
    import re
    if isinstance(soot_data_wrapped, dict) and "variables" in soot_data_wrapped:
        soot_data = soot_data_wrapped["variables"]
    else:
        soot_data = soot_data_wrapped

    # 1. Try to parse assertion with javalang to extract receivers (e.g. `obj.isBusy()` -> `obj`)
    var_names = set()
    getter_candidates = set()
    comparisons = []  # collected (left_id, operator, right_literal/var)
    try:
        wrapped = f"public class Wrapper {{ public void test() {{ {assertion if assertion.strip().endswith(';') else assertion + ';'} }}}}"
        tokens = javalang.tokenizer.tokenize(wrapped)
        parser = javalang.parser.Parser(tokens)
        tree = parser.parse()

        def _extract_id_from_expr(expr):
            """
            Recursively extract the left-most/base identifier from an expression.
            Examples:
            - for `signalKernel0.data` -> returns `signalKernel0`
            - for `obj.getX().field` -> returns `obj`
            - for simple string qualifiers -> returns the string
            """
            if expr is None:
                return None
            # Direct qualifier as string
            if isinstance(expr, str):
                return expr
            # MemberReference: prefer qualifier if present (could be nested MemberReference), else member
            if isinstance(expr, javalang.tree.MemberReference):
                qual = getattr(expr, 'qualifier', None)
                if qual:
                    return _extract_id_from_expr(qual)
                return getattr(expr, 'member', None)
            # MethodInvocation: recurse into its qualifier (e.g., obj.method())
            if isinstance(expr, javalang.tree.MethodInvocation):
                return _extract_id_from_expr(getattr(expr, 'qualifier', None))
            # Generic fallback: try to use a 'qualifier' attribute if present
            try:
                q = getattr(expr, 'qualifier', None)
                if q:
                    return _extract_id_from_expr(q)
                # try common name/member fields
                name = getattr(expr, 'member', None) or getattr(expr, 'name', None)
                if isinstance(name, str):
                    return name
            except Exception:
                return None
            return None

        for path, node in tree.filter(javalang.tree.MethodInvocation):
            q = getattr(node, 'qualifier', None)
            ident = _extract_id_from_expr(q)
            if ident:
                var_names.add(ident)
                
                # --- Anti-Hallucination check ---
                # Check if this method actually exists in the dynamic object's known getter list
                after_data = dynamic_data.get('after', {}).get(ident, {})
                obj_getters = set(after_data.get('getters', {}).keys())
                
                # If fields exist, try to collect public getters attached to those fields
                for f_k, f_v in (after_data.get('fields', {}) or {}).items():
                    if isinstance(f_v, dict) and f_v.get('getter'):
                        obj_getters.add(f_v['getter'])
                    elif isinstance(f_v, str) and '__ACCESSIBLE:' in f_v:
                        obj_getters.add(f_v.split('__ACCESSIBLE:')[1].strip())
                        
                # We generally allow boolean 'isX' methods to be checked cleanly, as they are standard getters,
                # as well as basic Collection methods (size, isEmpty) since EvoSuite captures them.
                # If it's a completely unknown method not captured by our dynamic trace getters,
                # we flag it to be penalized *softly* or leave it to compile-time check in eval.py.
                if node.member not in obj_getters and not getattr(node, 'selectors', []):
                     # Record the hallucinated method name to penalize it later
                     if not hasattr(score_assertion_from_statement, "hallucinated_methods"):
                         score_assertion_from_statement.hallucinated_methods = set()
                     # 宽泛判断：由于可能有标准库方法被误判，这里改成只记录，不强制在静态打分时设为 0
                     # 真正的幻觉方法会在后续的 javac 原程序编译测试中直接报错从而得到 0 分。
                     # score_assertion_from_statement.hallucinated_methods.add(ident + "." + node.member)
            else:
                # unqualified call (possible getter on implicit this): record method name to try mapping later
                getter_candidates.add(node.member)
            for arg in getattr(node, 'arguments', []) or []:
                # If the argument is a member reference (e.g., obj.field or arr[0]) try to extract the base qualifier first
                if isinstance(arg, javalang.tree.MemberReference):
                    base = _extract_id_from_expr(getattr(arg, 'qualifier', None))
                    if base:
                        var_names.add(base)
                    else:
                        # fallback: the member itself might be a local variable
                        if getattr(arg, 'member', None):
                            var_names.add(arg.member)
                elif isinstance(arg, javalang.tree.MethodInvocation):
                    q2 = _extract_id_from_expr(getattr(arg, 'qualifier', None))
                    if q2:
                        var_names.add(q2)
                    else:
                        getter_candidates.add(arg.member)

        for path, node in tree.filter(javalang.tree.MemberReference):
            # When seeing a MemberReference (e.g. signalKernel0.data or obj.field), prefer the base qualifier
            qual = getattr(node, 'qualifier', None)
            base = _extract_id_from_expr(qual)
            if base:
                var_names.add(base)
            else:
                if getattr(node, 'member', None):
                    var_names.add(node.member)

        # Extract binary comparisons (e.g., count > 0, size() >= 1)
        try:
            for path, node in tree.filter(javalang.tree.BinaryOperation):
                op = getattr(node, 'operator', None)
                left = getattr(node, 'lhs', None) or getattr(node, 'left', None)
                right = getattr(node, 'rhs', None) or getattr(node, 'right', None)
                l_id = _extract_id_from_expr(left)
                # right may be literal or member; try to extract value
                r_val = None
                try:
                    if hasattr(right, 'value'):
                        r_val = str(right.value)
                    else:
                        r_id = _extract_id_from_expr(right)
                        r_val = r_id
                except Exception:
                    r_val = str(right)
                if op and (l_id or r_val):
                    comparisons.append((l_id, op, r_val))
        except Exception:
            # Some javalang versions use different node names; try BinaryExpression as fallback
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
                            r_id = _extract_id_from_expr(right)
                            r_val = r_id
                    except Exception:
                        r_val = str(right)
                    if op and (l_id or r_val):
                        comparisons.append((l_id, op, r_val))
            except Exception:
                pass

    except Exception:
        # fallback to simple regex extraction if parsing fails
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
        assertion_clean = re.sub(r'".*?"|\'.*?\'|\b\d+(\.\d+[fFdD]?)?\b', '', assertion) # Also remove floats like 0.0f
        candidates = set(var_pattern.findall(assertion_clean))
        
        # Build reference of dynamic/static vars to filter out noise
        if isinstance(soot_data_wrapped, dict) and "variables" in soot_data_wrapped:
            known_vars = set(v.get("variable") for v in soot_data_wrapped["variables"])
        else:
            known_vars = set(v.get("variable") for v in (soot_data_wrapped if isinstance(soot_data_wrapped, list) else []))
            
        known_vars.update(dynamic_data.get('diff', {}).keys())
        known_vars.update(dynamic_data.get('after', {}).keys())

        for v in candidates:
            if v not in java_keywords and v in known_vars:
                var_names.add(v)

    # 1.5 If we found getter method names without explicit receiver, try mapping them to variables
    if getter_candidates and not var_names:
        after = dynamic_data.get('after', {})
        for g in getter_candidates:
            for var, st in after.items():
                # 1) check explicit getters mapping emitted by the probe
                getters_map = st.get('getters', {}) or {}
                if g in getters_map:
                    var_names.add(var)
                    break
                # 2) fallback: check field metadata for __ACCESSIBLE or parsed getter
                fields = st.get('fields', {}) or {}
                for f_k, f_v in fields.items():
                    if isinstance(f_v, dict):
                        if f_v.get('getter') == g or g in str(f_v.get('getter', '')):
                            var_names.add(var)
                            break
                    else:
                        if isinstance(f_v, str) and ('__ACCESSIBLE:' + g) in f_v:
                            var_names.add(var)
                            break
                if var in var_names:
                    break
    var_names = list(var_names)

    # 2. Score all variables using quantify_assertion_value
    var_scores = quantify_assertion_value(soot_data_wrapped, dynamic_data)

    # 3. Aggregate scores for variables involved in the assertion
    assertion_scores = {}
    total_score = 0
    logic_alignment_bonus = 0
    
    for v in var_names:
        if v in var_scores:
            v_data = var_scores[v]
            assertion_scores[v] = v_data
            total_score += v_data['total_score']
            
            # --- New Logic: Path Constraint Alignment Bonus ---
            # Check if the assertion text meaningfully touches the logic constraints defined by Soot
            soot_var_info = next((sv for sv in (soot_data if isinstance(soot_data, list) else []) if sv.get("variable") == v), {})
            if not soot_var_info and isinstance(soot_data_wrapped, dict):
                 # Try finding in wrapped structure
                 soot_var_info = next((sv for sv in soot_data_wrapped.get("variables", []) if sv.get("variable") == v), {})
            
            constraints = soot_var_info.get("control_flow_influence", {}).get("path_constraints", [])
            
            # Use sets to avoid applying the same bonus multiple times for duplicate constraints inherited
            applied_bonuses = set()
            
            for cond in constraints:
                cond_lower = cond.lower()
                assertion_lower = assertion.lower()

                # Heuristic 1: Constant matching (e.g., "0", "null", "true", "false")
                # Detects: assertEquals(0.0f, ...), assertNull(...)
                constants = re.findall(r'\b(null|true|false|0(\.0f)?)\b', cond_lower)
                for const_tuple in constants:
                    const = const_tuple[0]
                    if const in assertion_lower and f"const_{const}" not in applied_bonuses:
                        logic_alignment_bonus += 8.0
                        applied_bonuses.add(f"const_{const}")
                
                # Heuristic 2: Predicate/Operator alignment (High-value business logic)
                # Optimization 2: Logical Predicate Mapping (e.g., size() vs isEmpty())
                if any(op in cond_lower for op in ["size", "length", "empty", "0"]):
                    if any(target in assertion_lower for target in ["size", "length", "isempty", "hasmore", "next"]) and "size_empty" not in applied_bonuses:
                        logic_alignment_bonus += 10.0
                        applied_bonuses.add("size_empty")
                # Heuristic 2.5: Binary comparison alignment (e.g., 'count > 0' aligns with 'count >= 1' constraints)
                for (l_id, operator, r_val) in comparisons:
                    if not l_id:
                        continue
                    if l_id == v or l_id in cond_lower or l_id in assertion_lower:
                        # normalize operator tokens
                        op_tokens = [operator]
                        # check textual presence of operator or equivalent in constraint
                        if any(t in cond_lower for t in op_tokens) or any(str(r_val) in cond_lower for r_val in [r_val]):
                            key = f"comp_{l_id}_{operator}_{r_val}"
                            if key not in applied_bonuses:
                                logic_alignment_bonus += 8.0
                                applied_bonuses.add(key)
                
                # Heuristic 3: Exception/Negative logic alignment
                if "throw" in cond_lower or "null" in cond_lower:
                    if any(target in assertion_lower for target in ["assertnotnull", "assertsame", "notnull"]) and "not_null" not in applied_bonuses:
                        logic_alignment_bonus += 6.0
                        applied_bonuses.add("not_null")

                # Heuristic 4: Return value specific logic
                if "Return Value" in v_data.get("role", "") and "ret_bonus" not in applied_bonuses:
                     if any(c_word in assertion_lower for c_word in ["result", "val", "count", "idx", "asserttrue", "assertfalse", "assertequals"]):
                         logic_alignment_bonus += 4.0
                         applied_bonuses.add("ret_bonus")

    # Cap the logic bonus to avoid over-inflating minor assertions
    logic_alignment_bonus = min(logic_alignment_bonus, 35.0) # Increased cap from 25.0
    total_score += logic_alignment_bonus

    # --- Trivial / "Water" Assertion Penalty ---
    # Heavily penalize assertions that do not check internal state or values effectively
    if re.match(r'^\s*assert(Not)?Null\s*\(', assertion):
        total_score *= 0.1  # These assertions almost never kill mutants (except crash/null bugs)
    elif re.match(r'^\s*assert(True|False)\s*\(\s*[a-zA-Z0-9_.]+\s*(==|!=)\s*null\s*\)', assertion):
        total_score *= 0.1

    # --- Anti-Hallucination check penalty ---
    # Currently disabled: Let dynamic compilation (eval.py) handle true hallucinations to avoid
    # killing legitimate JDK methods like 'length()', 'hasMoreElements()'.
    if hasattr(score_assertion_from_statement, "hallucinated_methods") and score_assertion_from_statement.hallucinated_methods:
        # We no longer strictly zero out score here.
        score_assertion_from_statement.hallucinated_methods.clear()

    # If no variable matches, optionally return all scores or 0
    if not assertion_scores:
        return {
            'assertion': assertion,
            'variables': var_names,
            'matched_scores': {},
            'total_score': 0,
            'logic_alignment_bonus': 0,
            'note': 'No variables in assertion matched analysis results.'
        }
    return {
        'assertion': assertion,
        'variables': var_names,
        'matched_scores': assertion_scores,
        'logic_alignment_bonus': logic_alignment_bonus,
        'total_score': total_score
    }

def fast_score(matched_scores, logic_bonus, weights):
    max_v_score = 0
    for var, v_info in matched_scores.items():
        breakdown = v_info.get('breakdown', {})
        v_score = (
            breakdown.get('Role', 0) * weights['role'] +
            breakdown.get('Modification', 0) * weights['mod'] +
            breakdown.get('Impact', 0) * weights['impact'] +
            breakdown.get('Complexity', 0) * weights['comp']
        )
        max_v_score = max(max_v_score, v_score)
    return max_v_score + logic_bonus

def run_optimization(full_search=False, n_iter=300, objective='spearman', filter_zero=False, pos_weight=1.0):
    results_path = "excution_deepseek_generated_predictions.json"
    if not os.path.exists(results_path): return
    with open(results_path, 'r') as f: data = json.load(f)
    samples = []
    
    # 获取第一个item的长度确认
    total_indices = len(data[0].get('ds_rewards', []))
    print(f"Data array length: {total_indices} (All indices 0-{total_indices-1} are DeepSeek generated)")

    for item in data:
        rewards = item.get('ds_rewards', [])
        ds_asserts = item.get('ds_generates', [])

        # 既然 index 0 也是生成的，那么遍历 0 到 len(rewards)-1
        for k in range(len(rewards)):
            r = rewards[k]
            q = None

            # 优先使用 ds_generates 中的断言文本来重新计算分数；回退到单个 item['assert']（如果只有一个断言）
            assertion = None
            if isinstance(ds_asserts, list) and k < len(ds_asserts):
                assertion = ds_asserts[k]
            elif 'assert' in item and k == 0:
                assertion = item.get('assert')

            if assertion:
                try:
                    q = score_assertion_from_statement(assertion, item.get('soot_analysis_result', {}), item.get('dynamic_analysis', {}))
                except Exception:
                    q = None

            if r is not None and isinstance(q, dict):
                samples.append({
                    'reward': float(r),
                    'matched_scores': q.get('matched_scores', {}),
                    'logic_bonus': q.get('logic_alignment_bonus', 0),
                    'assertion': assertion,
                    'soot': item.get('soot_analysis_result', {}),
                    'dynamic': item.get('dynamic_analysis', {})
                })

    print(f"Total valid DeepSeek assertion samples: {len(samples)}")
    if filter_zero:
        samples = [s for s in samples if s['reward'] > 0]
        print(f"After filtering zeros, samples: {len(samples)}")
    if len(samples) < 20:
        print("Too few samples.")
        return

    y = np.array([s['reward'] for s in samples])
    # keep a copy of full samples for final validation
    full_samples = list(samples)
    
    # If requested, run an extended random search over the full `weights` parameter space
    if full_search:
        import random
        # search operates on `samples` (which may be filtered); keep full_samples for final validation
        search_samples = samples
        y_search = np.array([s['reward'] for s in search_samples])
        # sample weights: boost positives by pos_weight
        sample_wts = np.array([pos_weight if s['reward'] > 0 else 1.0 for s in search_samples])

        best_spearman = -1.0
        best_weights = None
        for i in range(n_iter):
            cand = {
                "role_return_no_side_effects": int(random.uniform(10, 40)),
                "role_return_with_side_effects": int(random.uniform(5, 30)),
                "role_focal": int(random.uniform(5, 30)),
                "role_input": int(random.uniform(1, 10)),
                "role_other": int(random.uniform(0, 5)),
                "mod_new": int(random.uniform(10, 40)),
                "mod_accessible": int(random.uniform(10, 35)),
                "mod_field": int(random.uniform(5, 30)),
                "mod_none": 0,
                "impact_branch_weight": float(random.uniform(0.5, 2.0)),
                "impact_cap": int(random.uniform(5, 25)),
                "impact_nonmodified_multiplier": float(random.uniform(0.05, 0.5)),
                "complexity_fields": float(random.uniform(0.5, 5.0)),
                "complexity_elements": float(random.uniform(0.5, 5.0)),
                "complexity_deep_fields": float(random.uniform(2.0, 10.0)),
                "complexity_static_fields": float(random.uniform(0.5, 5.0)),
                "bonus_return_no_side_effects": float(random.uniform(0.0, 10.0)),
                "penalty_focal_return_unmodified": float(random.uniform(-10.0, 0.0))
            }
            # use full `cand` weights: temporarily replace module `weights` and recompute full scores
            old_weights = weights.copy()
            try:
                weights.clear()
                weights.update(cand)
            except Exception:
                weights.clear()
                weights.update(old_weights)
                continue

            x_vals = []
            try:
                for s in search_samples:
                    try:
                        scored = score_assertion_from_statement(s['assertion'], s.get('soot', {}), s.get('dynamic', {}))
                        x_vals.append(float(scored.get('total_score', 0.0)))
                    except Exception:
                        x_vals.append(0.0)
                x = np.array(x_vals)
            except Exception:
                # restore weights and skip candidate
                weights.clear()
                weights.update(old_weights)
                continue
            # choose objective
            if objective == 'spearman':
                from scipy import stats as _stats
                res = _stats.spearmanr(x, y_search)
                score_val = res.correlation if hasattr(res, 'correlation') else (res[0] if res else None)
                if score_val is None:
                    continue
            elif objective.startswith('precision@'):
                try:
                    k = int(objective.split('@')[1])
                except Exception:
                    k = 20
                idxs = np.argsort(-x)
                top_idxs = idxs[:k]
                top_rewards = y_search[top_idxs]
                top_w = sample_wts[top_idxs]
                numer = float(np.sum(top_w * (top_rewards > 0)))
                denom = float(np.sum(top_w)) if float(np.sum(top_w)) > 0 else 1.0
                score_val = numer / denom
            else:
                # default fallback to spearman
                from scipy import stats as _stats
                res = _stats.spearmanr(x, y)
                score_val = res.correlation if hasattr(res, 'correlation') else (res[0] if res else None)
                if score_val is None:
                    continue
            if score_val > best_spearman:
                best_spearman = score_val
                best_weights = cand.copy()

            # restore original weights for next iteration
            weights.clear()
            weights.update(old_weights)
        print("RANDOM_SEARCH_DONE")
        print(f"BEST_SCORE ({objective}): {best_spearman:.4f}")
        print(f"BEST_WEIGHTS: {best_weights}")
        # final diagnostics with best_weights by recomputing full scores on full_samples
        if best_weights:
            old_weights = weights.copy()
            try:
                weights.clear()
                weights.update(best_weights)
                x_full = []
                for s in full_samples:
                    try:
                        scored = score_assertion_from_statement(s['assertion'], s.get('soot', {}), s.get('dynamic', {}))
                        x_full.append(float(scored.get('total_score', 0.0)))
                    except Exception:
                        x_full.append(0.0)
                x_full = np.array(x_full)
                y_full = np.array([s['reward'] for s in full_samples])
                try:
                    p_corr, _ = stats.pearsonr(x_full, y_full)
                except Exception:
                    p_corr = float('nan')
                print(f"PEARSON (full): {p_corr:.4f}")
                threshold = np.percentile(x_full, 80)
                top_group = y_full[x_full >= threshold]
                bottom_group = y_full[x_full < threshold]
                print(f"HIT_RATE_TOP20 (full): {np.mean(top_group > 0):.4f}")
                print(f"HIT_RATE_BOTTOM80 (full): {np.mean(bottom_group > 0):.4f}")
            finally:
                weights.clear()
                weights.update(old_weights)
        return

    # 权重搜索空间 (legacy small grid)
    role_space = [0.1, 0.5, 1.0]
    mod_space = [1.5, 2.0, 3.0, 5.0]
    impact_space = [0.1, 0.5, 1.0]
    comp_space = [0.1, 0.5, 1.0]
    
    best_spearman = -1
    best_weights = {}

    combinations = list(itertools.product(role_space, mod_space, impact_space, comp_space))
    for r_w, m_w, i_w, c_w in combinations:
        w = {'role': r_w, 'mod': m_w, 'impact': i_w, 'comp': c_w}
        x = np.array([fast_score(s['matched_scores'], s['logic_bonus'], w) for s in samples])
        if np.std(x) < 1e-6: continue
        corr, _ = stats.spearmanr(x, y)
        if corr > best_spearman:
            best_spearman = corr
            best_weights = w

    print(f"SEARCH_DONE")
    print(f"SPEARMAN: {best_spearman:.4f}")
    print(f"WEIGHTS: {best_weights}")
    
    best_x = np.array([fast_score(s['matched_scores'], s['logic_bonus'], best_weights) for s in samples])
    p_corr, _ = stats.pearsonr(best_x, y)
    print(f"PEARSON: {p_corr:.4f}")
    
    threshold = np.percentile(best_x, 80)
    top_group = y[best_x >= threshold]
    bottom_group = y[best_x < threshold]
    print(f"HIT_RATE_TOP20: {np.mean(top_group > 0):.4f}")
    print(f"HIT_RATE_BOTTOM80: {np.mean(bottom_group > 0):.4f}")

if __name__ == "__main__":
    run_optimization()
