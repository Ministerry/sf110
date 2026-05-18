import os
import json
import shutil
import re
import subprocess
import glob
import javalang
from filelock import FileLock
import time
import traceback
import tempfile
import argparse
import datetime
import json
from typing import Optional
from utils import *
from train_utils import *
import sys

CWD = "/home/ubuntu/myren/SF110"
STATIC_ANALYSIS_VERSION = "static_v2"

def parse_bool_arg(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

# --- Soot Analysis Helper Functions ---
def fuzzy_match_signature(target, candidates):
    def parse_sig(s):
        try:
            parts = s.split(' ', 1)
            ret_type = parts[0]
            if len(parts) > 1:
                rest = parts[1]
                if '(' not in rest: return None, None, None
                name = rest.split('(')[0]
                params_str = rest.split('(')[1].rstrip(')')
                params = [p.strip() for p in params_str.split(',')] if params_str else []
            else:
                 return None, None, None
            return ret_type, name, params
        except:
             return None, None, None

    t_ret, t_name, t_params = parse_sig(target)
    if not t_name: return None
    
    matches = []
    for cand in candidates:
        c_ret, c_name, c_params = parse_sig(cand)
        if not c_name: continue
        
        if c_name == t_name and len(c_params) == len(t_params):
            params_match = True
            for tp, cp in zip(t_params, c_params):
                # 1. Direct substring match (legacy)
                tp_base = tp.replace('[]', '')
                cp_base = cp.replace('[]', '')
                direct_match = (tp_base in cp_base or cp_base in tp_base)
                
                # 2. Simple Name Match (New)
                # Handle full paths: java.io.File vs File
                tp_simple = tp_base.split('.')[-1]
                cp_simple = cp_base.split('.')[-1]
                simple_match = (tp_simple == cp_simple)
                
                if not (direct_match or simple_match):
                     params_match = False
                     break
                if tp.count('[]') != cp.count('[]'):
                    params_match = False
                    break
            if params_match:
                matches.append(cand)
    if len(matches) >= 1:
        return matches[0]
    return None


def _metric_snapshot(metric):
    return {
        "reads": metric.get("reads", 0),
        "writes": metric.get("writes", 0),
        "branch_usage": metric.get("branch_usage", 0),
        "exception_usage": metric.get("exception_usage", 0),
        "branch_condition_usage": metric.get("branch_condition_usage", 0),
        "pdg_influence": metric.get("pdg_influence", 0),
        "path_constraints": list(metric.get("path_constraints", [])),
    }


def _summarize_named_metrics(metrics, prefix):
    summary = {}
    for key, metric in metrics.items():
        if key.startswith(prefix):
            summary[key.replace(prefix, "", 1)] = _metric_snapshot(metric)
    return summary


def _summarize_raw_soot(soot_data):
    units = soot_data.get("units", []) if isinstance(soot_data, dict) else []
    pdg = soot_data.get("pdg", {}) if isinstance(soot_data, dict) else {}
    return {
        "unit_count": len(units),
        "branch_unit_count": sum(1 for unit in units if unit.get("is_branch", False)),
        "return_unit_count": sum(1 for unit in units if str(unit.get("content", "")).strip().startswith("return")),
        "throw_unit_count": sum(
            1 for unit in units
            if str(unit.get("content", "")).strip().startswith("throw")
        ),
        "invoke_unit_count": sum(1 for unit in units if "invoke" in str(unit.get("content", ""))),
        "pdg_node_count": len(pdg.get("nodes", [])),
        "pdg_edge_count": len(pdg.get("edges", [])),
        "dominator_entry_count": len(soot_data.get("dominators", [])) if isinstance(soot_data, dict) else 0,
    }


def _summarize_return_flow(return_sites):
    origin_counts = {}
    for site in return_sites:
        for origin in site.get("origins", []):
            origin_counts[origin] = origin_counts.get(origin, 0) + 1
    return {
        "return_site_count": len(return_sites),
        "origin_counts": origin_counts,
        "sites": return_sites,
    }

def _summarize_branch_sites(branch_sites):
    branch_sites = branch_sites or []
    origin_counts = {}
    conditions = []
    unique_conditions = []
    for site in branch_sites:
        cond = site.get("condition")
        if cond is not None:
            conditions.append(cond)
            if cond not in unique_conditions:
                unique_conditions.append(cond)
        for origin in site.get("origins", []):
            origin_counts[origin] = origin_counts.get(origin, 0) + 1
    return {
        "branch_site_count": len(branch_sites),
        "condition_count": len(conditions),
        "unique_condition_count": len(unique_conditions),
        "conditions": conditions,
        "unique_conditions": unique_conditions,
        "origin_counts": origin_counts,
        "sites": branch_sites,
    }

def _summarize_metric_group(metrics_by_name):
    metrics_by_name = metrics_by_name or {}
    totals = {
        "field_count": len(metrics_by_name),
        "field_names": sorted(metrics_by_name.keys()),
        "reads": 0,
        "writes": 0,
        "branch_usage": 0,
        "exception_usage": 0,
        "branch_condition_usage": 0,
        "pdg_influence": 0,
        "path_constraints": [],
        "active_field_count": 0,
        "max_reads": 0,
        "max_writes": 0,
        "max_pdg_influence": 0,
    }
    for metric in metrics_by_name.values():
        reads = metric.get("reads", 0)
        writes = metric.get("writes", 0)
        branch_usage = metric.get("branch_usage", 0)
        exception_usage = metric.get("exception_usage", 0)
        branch_condition_usage = metric.get("branch_condition_usage", 0)
        pdg_influence = metric.get("pdg_influence", 0)

        totals["reads"] += reads
        totals["writes"] += writes
        totals["branch_usage"] += branch_usage
        totals["exception_usage"] += exception_usage
        totals["branch_condition_usage"] += branch_condition_usage
        totals["pdg_influence"] += pdg_influence
        totals["max_reads"] = max(totals["max_reads"], reads)
        totals["max_writes"] = max(totals["max_writes"], writes)
        totals["max_pdg_influence"] = max(totals["max_pdg_influence"], pdg_influence)
        if reads > 0 or writes > 0:
            totals["active_field_count"] += 1
        for pc in metric.get("path_constraints", []) or []:
            if pc not in totals["path_constraints"]:
                totals["path_constraints"].append(pc)
    totals["path_constraint_count"] = len(totals["path_constraints"])
    return totals

def _summarize_variable_roles(results):
    results = results or []
    role_counts = {}
    role_variables = {}
    focal_object = None
    return_values = []
    input_arguments = []
    constructor_arguments = []

    for entry in results:
        role = entry.get("role", "")
        variable = entry.get("variable")
        role_counts[role] = role_counts.get(role, 0) + 1
        role_variables.setdefault(role, []).append(variable)
        if role == "Focal Object (State Owner)" and focal_object is None:
            focal_object = entry
        elif role == "Return Value (Output)":
            return_values.append(variable)
        elif role == "Input Argument":
            input_arguments.append(variable)
        elif role == "Constructor Argument (State Initializer)":
            constructor_arguments.append(variable)

    focal_summary = {}
    if focal_object:
        cfi = focal_object.get("control_flow_influence", {}) or {}
        dfa = focal_object.get("data_flow_state_access", {}) or {}
        focal_summary = {
            "variable": focal_object.get("variable"),
            "branch_decisions_dependent_on_state": cfi.get("branch_decisions_dependent_on_state", 0),
            "exception_paths": cfi.get("exception_paths", 0),
            "branch_condition_usage": cfi.get("branch_condition_usage", 0),
            "pdg_dependants": cfi.get("pdg_dependants", 0),
            "path_constraint_count": len(cfi.get("path_constraints", []) or []),
            "fields_read": dfa.get("fields_read", 0),
            "fields_modified": dfa.get("fields_modified", 0),
            "field_count": len(dfa.get("fields_list", []) or []),
            "field_metric_count": len(dfa.get("field_metrics", {}) or {}),
            "static_fields_read": dfa.get("static_fields_read", 0),
            "static_fields_modified": dfa.get("static_fields_modified", 0),
            "static_field_count": len(dfa.get("static_fields_list", []) or []),
            "static_field_metric_count": len(dfa.get("static_field_metrics", {}) or {}),
        }

    return {
        "tracked_variable_count": len(results),
        "role_counts": role_counts,
        "role_variables": role_variables,
        "focal_object": focal_summary,
        "return_value_variables": return_values,
        "input_argument_variables": input_arguments,
        "constructor_argument_variables": constructor_arguments,
    }

def analyze_variable_flow(entry):
    project_dir_name = f"{entry['bug_num']}_{entry['project']}"
    project_root_override = entry.get("_project_path_override") or entry.get("_project_root_override")
    project_base_path = project_root_override or os.path.join(CWD, project_dir_name)
    project_classes_path = entry.get("_classes_path_override") or os.path.join(project_base_path, "target/classes")
    
    # Try finding classes path
    if not os.path.exists(project_classes_path):
        base_path = project_base_path
        possible_paths = [
            os.path.join(base_path, "target/classes"),
            os.path.join(base_path, "build/classes"),
            os.path.join(base_path, "bin")
        ]
        for p in possible_paths:
            if os.path.exists(p):
                project_classes_path = p
                break
    
    if not os.path.exists(project_classes_path):
        return {"error": "Classes not found"}

    full_class_name = entry['test_name']
    method_name = entry['extracted_method_name']
    target_method_simple_name = method_name

    # Inferred Signature Logic
    if 'focal_method' in entry and entry.get('focal_method'):
        wrapped = f"public class Wrapper {{ {entry['focal_method']} }}"
        try:
            tokens = javalang.tokenizer.tokenize(wrapped)
            parser = javalang.parser.Parser(tokens)
            tree = parser.parse()
            for path, node in tree:
                if isinstance(node, javalang.tree.MethodDeclaration):
                    if node.name == target_method_simple_name:
                        ret = "void"
                        if node.return_type:
                            ret = node.return_type.name
                            if hasattr(node.return_type, 'dimensions') and node.return_type.dimensions:
                                ret += "[]" * len(node.return_type.dimensions)
                        params = []
                        if node.parameters:
                            for p in node.parameters:
                                t = p.type.name
                                if hasattr(p.type, 'dimensions') and p.type.dimensions:
                                    t += "[]" * len(p.type.dimensions)
                                if getattr(p, 'varargs', False):
                                    t += "[]"
                                params.append(t)
                        method_name = f"{ret} {node.name}({','.join(params)})"
                        break
        except Exception as sig_error:
             print(f"[WARN] Failed to infer full focal signature, fallback to method name: {sig_error}")

    analyzer_jar = os.path.join(CWD, "soot_analysis/target/soot-analyzer-1.0-SNAPSHOT.jar")
    if not os.path.exists(analyzer_jar):
        return {"error": "Soot jar not found"}
        
    safe_project = re.sub(r"[^A-Za-z0-9_.-]", "_", str(entry.get('project', 'unknown')))
    fd, output_file = tempfile.mkstemp(
        prefix=f"soot_output_{entry['bug_num']}_{safe_project}_",
        suffix=".json"
    )
    os.close(fd)
    if os.path.exists(output_file):
        os.remove(output_file)
    cmd = ["java", "-jar", analyzer_jar, project_classes_path, full_class_name, method_name, output_file]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if result.returncode != 0:
            stderr_output = result.stderr
            print(f"[ERROR-SOOT] {stderr_output}")  # Added logging
            if "Available methods:" in stderr_output:
                available_sigs = []
                for line in stderr_output.split('\n'):
                    line = line.strip()
                    if '<' in line and '>' in line:
                         start = line.find('<')
                         end = line.rfind('>')
                         if start != -1 and end != -1:
                             content = line[start+1:end]
                             if ': ' in content:
                                 available_sigs.append(content.split(': ', 1)[1])
                corrected_sig = fuzzy_match_signature(method_name, available_sigs)
                if corrected_sig:
                    print(f"[INFO] Retrying Soot with signature: {corrected_sig}")
                    cmd[5] = corrected_sig
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
                    if result.returncode != 0:
                         print(f"[ERROR-SOOT-RETRY] {result.stderr}")

            # Check for inner classes
            if result.returncode != 0 and "No method" in result.stderr:
                try: 
                    # Try finding inner classes of current full_class_name
                    base_path = full_class_name.replace('.', '/')
                    search_glob = os.path.join(project_classes_path, base_path + "$*.class")
                    inner_classes = glob.glob(search_glob)
                    for ic_path in inner_classes:
                        # Construct class name
                        rel = os.path.relpath(ic_path, project_classes_path)
                        ic_name = rel.replace(os.sep, '.').rsplit('.', 1)[0]
                        print(f"[INFO] Retrying Soot with inner class: {ic_name}")
                        cmd_inner = list(cmd) # Copy
                        cmd_inner[4] = ic_name # Update class name arg
                        # Use method_name (signature) to ensure fuzzy matcher works if needed
                        cmd_inner[5] = method_name 
                        
                        res_inner = subprocess.run(cmd_inner, capture_output=True, text=True, timeout=90)
                        if res_inner.returncode == 0:
                            result = res_inner
                            print(f"[INFO] Soot success with inner class: {ic_name}")
                            break
                        else:
                             # Try fuzzy match on inner class?
                             stderr_inner = res_inner.stderr
                             if "Available methods:" in stderr_inner:
                                available_sigs_inner = []
                                for line in stderr_inner.split('\n'):
                                    line = line.strip()
                                    if '<' in line and '>' in line:
                                         start = line.find('<')
                                         end = line.rfind('>')
                                         if start != -1 and end != -1:
                                             content = line[start+1:end]
                                             if ': ' in content:
                                                 available_sigs_inner.append(content.split(': ', 1)[1])
                                corrected_sig_inner = fuzzy_match_signature(method_name, available_sigs_inner)
                                if corrected_sig_inner:
                                    print(f"[INFO] Retrying Inner Class Soot with signature: {corrected_sig_inner}")
                                    cmd_inner[5] = corrected_sig_inner
                                    res_inner_retry = subprocess.run(cmd_inner, capture_output=True, text=True, timeout=90)
                                    if res_inner_retry.returncode == 0:
                                        result = res_inner_retry
                                        print(f"[INFO] Soot success with inner class fuzzy: {ic_name}")
                                        break
                except Exception as e:
                    print(f"[WARN] Inner class search failed: {e}")

        if result.returncode != 0 or not os.path.exists(output_file):
            if os.path.exists(output_file):
                os.remove(output_file)
            return {"error": f"Soot analysis failed: {result.stderr if result.returncode != 0 else 'Output file missing'}"}
            
        with open(output_file, 'r') as f:
            soot_data = json.load(f)
        if os.path.exists(output_file):
            os.remove(output_file)
            
        metrics, flow_summary = calculate_metrics_from_soot(soot_data)
        
        # Analyze Path and PDG global metrics
        global_soot_info = {
            "total_estimated_paths": soot_data.get("total_estimated_paths", 0),
            "pdg_node_count": len(soot_data.get("pdg", {}).get("nodes", []))
        }
        
        # Analyze Prefix
        prefix = entry.get('prefix', '')
        focal_prefix = entry.get('focal_prefix', '')
        assertion_text = entry.get('assert', '')
        focal_obj = None
        var_role_map = {}
        focal_constructor_args = []
        
        wrapped_prefix = f"public class Wrapper {{ public void test() {{ {prefix} }} }}"
        try:
            tokens = javalang.tokenizer.tokenize(wrapped_prefix)
            parser = javalang.parser.Parser(tokens)
            tree = parser.parse()
            
            simple_name = entry['extracted_method_name']
            
            for path, node in tree.filter(javalang.tree.MethodInvocation):
                if node.member == simple_name:
                    for i, arg in enumerate(node.arguments):
                        if isinstance(arg, javalang.tree.MemberReference):
                             var_role_map[arg.member] = f"PARAM_{i}"
                    if not focal_obj and node.qualifier:
                        focal_obj = node.qualifier

            if focal_obj:
                for path, node in tree.filter(javalang.tree.VariableDeclarator):
                    if node.name == focal_obj and node.initializer:
                        if isinstance(node.initializer, javalang.tree.ClassCreator):
                            for arg in node.initializer.arguments:
                                if isinstance(arg, javalang.tree.MemberReference):
                                    focal_constructor_args.append(arg.member)
            
            for path, node in tree.filter(javalang.tree.LocalVariableDeclaration):
                 for decl in node.declarators:
                     if isinstance(decl.initializer, javalang.tree.MethodInvocation):
                         if decl.initializer.member == simple_name:
                             var_role_map[decl.name] = "RETURN"

            if not focal_obj:
                 m_name = simple_name
                 pattern = r"([a-zA-Z0-9_$]+)\s*\.\s*" + re.escape(m_name) + r"\s*\("
                 source_texts = [prefix, focal_prefix, assertion_text]
                 hits = []
                 for text in source_texts:
                     if not text:
                         continue
                     for m in re.finditer(pattern, text):
                         hits.append(m.group(1))
                 if hits:
                     # Deterministic: pick the most frequent receiver; tie -> first seen.
                     counts = {}
                     order = {}
                     for i, h in enumerate(hits):
                         counts[h] = counts.get(h, 0) + 1
                         if h not in order:
                             order[h] = i
                     focal_obj = sorted(counts.keys(), key=lambda k: (-counts[k], order[k]))[0]

            if focal_obj: var_role_map[focal_obj] = "THIS"

        except: pass

        role_to_var = {v: k for k, v in var_role_map.items()}
        
        field_metrics = {'reads': 0, 'writes': 0, 'branch': 0, 'exception': 0, 'branch_condition_usage': 0, 'pdg_influence': 0}
        static_field_metrics = {'reads': 0, 'writes': 0, 'branch': 0, 'exception': 0, 'branch_condition_usage': 0, 'pdg_influence': 0}
        field_metrics_by_name = _summarize_named_metrics(metrics, "FIELD_")
        static_field_metrics_by_name = _summarize_named_metrics(metrics, "STATIC_FIELD_")
        accessed_fields = []
        accessed_static_fields = []
        for k, v in metrics.items():
            if k.startswith("FIELD_"):
                field_metrics['reads'] += v['reads']
                field_metrics['writes'] += v['writes']
                field_metrics['branch'] += v['branch_usage']
                field_metrics['exception'] += v.get('exception_usage', 0)
                field_metrics['branch_condition_usage'] += v.get('branch_condition_usage', 0)
                field_metrics['pdg_influence'] += v.get('pdg_influence', 0)
                # Aggregate constraints from fields
                if 'path_constraints' not in field_metrics:
                    field_metrics['path_constraints'] = []
                for pc in v.get('path_constraints', []):
                    if pc not in field_metrics['path_constraints']:
                        field_metrics['path_constraints'].append(pc)
                
                if v['reads'] > 0 or v['writes'] > 0:
                    accessed_fields.append(k.replace("FIELD_", ""))
            elif k.startswith("STATIC_FIELD_"):
                static_field_metrics['reads'] += v['reads']
                static_field_metrics['writes'] += v['writes']
                static_field_metrics['branch'] += v['branch_usage']
                static_field_metrics['exception'] += v.get('exception_usage', 0)
                static_field_metrics['branch_condition_usage'] += v.get('branch_condition_usage', 0)
                static_field_metrics['pdg_influence'] += v.get('pdg_influence', 0)
                # Aggregate constraints from static fields
                if 'path_constraints' not in static_field_metrics:
                    static_field_metrics['path_constraints'] = []
                for pc in v.get('path_constraints', []):
                    if pc not in static_field_metrics['path_constraints']:
                        static_field_metrics['path_constraints'].append(pc)
                
                if v['reads'] > 0 or v['writes'] > 0:
                    accessed_static_fields.append(k.replace("STATIC_FIELD_", ""))

        results = []

        # 1. FOCAL OBJECT
        var_name = role_to_var.get("THIS", focal_obj if focal_obj else "(Implicit/Unknown)")
        m_this = metrics.get('THIS', {})
        this_branches = m_this.get('branch_usage', 0)
        this_exceptions = m_this.get('exception_usage', 0)
        
        results.append({
            "variable": var_name,
            "role": "Focal Object (State Owner)",
            "control_flow_influence": {
                "branch_decisions_dependent_on_state": field_metrics['branch'] + static_field_metrics['branch'] + this_branches,
                "exception_paths": field_metrics['exception'] + static_field_metrics['exception'] + this_exceptions,
                "branch_condition_usage": m_this.get('branch_condition_usage', 0) + field_metrics.get('branch_condition_usage', 0),
                "pdg_dependants": m_this.get('pdg_influence', 0) + field_metrics.get('pdg_influence', 0),
                "path_constraints": list(set(m_this.get('path_constraints', []) + field_metrics.get('path_constraints', []) + static_field_metrics.get('path_constraints', [])))
            },
            "data_flow_state_access": {
                "fields_read": field_metrics['reads'],
                "fields_modified": field_metrics['writes'],
                "fields_list": accessed_fields,
                "field_metrics": field_metrics_by_name,
                "static_fields_read": static_field_metrics['reads'],
                "static_fields_modified": static_field_metrics['writes'],
                "static_fields_list": accessed_static_fields,
                "static_field_metrics": static_field_metrics_by_name,
            }
        })

        # 1.5 CONSTRUCTOR ARGS
        for c_arg in focal_constructor_args:
            if c_arg not in var_role_map:
                results.append({
                    "variable": c_arg,
                    "role": "Constructor Argument (State Initializer)",
                    "flow_influence": {
                        "initializes_state_used_in_branches": field_metrics['branch'],
                        "initializes_state_used_in_exceptions": field_metrics['exception'],
                        "backing_data_read": field_metrics['reads'],
                        "backing_data_modified": field_metrics['writes']
                    }
                })

        # 2. ARGUMENTS
        param_roles = [k for k in metrics.keys() if k.startswith("PARAM_")]
        param_roles.sort()
        for p_role in param_roles:
            p_var = role_to_var.get(p_role, "(Literal/Expression)")
            m = metrics[p_role]
            results.append({
                "variable": p_var,
                "role": "Input Argument",
                "control_flow_influence": {
                    "branch_decisions": m['branch_usage'],
                    "exception_paths": m.get('exception_usage', 0),
                    "branch_condition_usage": m.get('branch_condition_usage', 0),
                    "pdg_dependants": m.get('pdg_influence', 0),
                    "path_constraints": m.get('path_constraints', [])
                },
                "data_flow_usage": {
                    "reads": m['reads'],
                    "writes": m['writes']
                }
            })

        # 3. RETURN VALUE
        ret_var = role_to_var.get("RETURN")
        if ret_var:
            results.append({
                "variable": ret_var,
                "role": "Return Value (Output)",
                "flow_sources": "Result of computation",
                "return_flow": _summarize_return_flow(flow_summary.get("return_sites", [])),
            })

        branch_summary = _summarize_branch_sites(flow_summary.get("branch_sites", []))
        return_summary = _summarize_return_flow(flow_summary.get("return_sites", []))
        field_instance_summary = _summarize_metric_group(field_metrics_by_name)
        field_static_summary = _summarize_metric_group(static_field_metrics_by_name)
        variable_summary = _summarize_variable_roles(results)
        raw_summary = _summarize_raw_soot(soot_data)
        method_level_summary = {
            "unit_count": raw_summary.get("unit_count", 0),
            "branch_unit_count": raw_summary.get("branch_unit_count", 0),
            "return_unit_count": raw_summary.get("return_unit_count", 0),
            "throw_unit_count": raw_summary.get("throw_unit_count", 0),
            "invoke_unit_count": raw_summary.get("invoke_unit_count", 0),
            "pdg_node_count": raw_summary.get("pdg_node_count", 0),
            "pdg_edge_count": raw_summary.get("pdg_edge_count", 0),
            "dominator_entry_count": raw_summary.get("dominator_entry_count", 0),
            "estimated_paths": global_soot_info.get("total_estimated_paths", 0),
            "branch_site_count": branch_summary.get("branch_site_count", 0),
            "return_site_count": return_summary.get("return_site_count", 0),
            "return_constant_site_count": sum(1 for site in return_summary.get("sites", []) or [] if site.get("returns_constant")),
            "return_has_constant": any(site.get("returns_constant") for site in return_summary.get("sites", []) or []),
        }

        analysis_result = {
            "analysis_version": STATIC_ANALYSIS_VERSION,
            "analyzed_class_name": soot_data.get("class_name"),
            "analyzed_method_name": soot_data.get("method_name"),
            "variables": results,
            "global_metrics": global_soot_info,
            "field_metrics": {
                "instance": field_metrics_by_name,
                "static": static_field_metrics_by_name,
            },
            "return_flow": return_summary,
            "branch_sites": flow_summary.get("branch_sites", []),
            "raw_summary": raw_summary,
            "analysis_dimensions": {
                "method_level": method_level_summary,
                "point_level": {
                    "branch": branch_summary,
                    "return": return_summary,
                },
                "variable_level": variable_summary,
                "field_level": {
                    "instance": field_instance_summary,
                    "static": field_static_summary,
                },
            },
        }
        if parse_bool_arg(os.getenv("SOOT_INCLUDE_RAW_OUTPUT"), default=False):
            analysis_result["raw_soot"] = soot_data
        return analysis_result

    except Exception as e:
        if os.path.exists(output_file):
            os.remove(output_file)
        return {"error": str(e)}

def calculate_metrics_from_soot(soot_data):
    if not soot_data:
        return {}, {"return_sites": [], "branch_sites": []}
    units = soot_data.get('units', [])
    metrics = {}
    local_role_map = {} 
    flow_summary = {"return_sites": [], "branch_sites": []}
    
    for unit in units:
        content = unit.get('content', '')
        if ":= @this" in content:
            defs = unit.get('defs', [])
            if defs: local_role_map[defs[0]['name']] = 'THIS'
        elif ":= @parameter" in content:
            try:
                param_part = content.split(':= @parameter')[1].split(':')[0]
                index = int(param_part)
                defs = unit.get('defs', [])
                if defs: local_role_map[defs[0]['name']] = f"PARAM_{index}"
            except: pass

    def add_metric(key, field, amount=1, detail=None):
        if key not in metrics:
            metrics[key] = {
                'reads': 0, 'writes': 0, 'branch_usage': 0, 'exception_usage': 0, 
                'branch_condition_usage': 0, 'pdg_influence': 0,
                'path_constraints': []
            }
        
        if field == 'path_constraints' and detail:
            if detail not in metrics[key]['path_constraints']:
                metrics[key]['path_constraints'].append(detail)
        else:
            metrics[key][field] += amount

    var_origins = {}
    for name, role in local_role_map.items(): var_origins[name] = {role}

    def is_likely_readonly_invoke(content):
        # Jimple call text usually embeds method name in '<Class: RetType method(...)>'
        m = re.search(r":\s+[\w\.$\[\]]+\s+([A-Za-z0-9_$]+)\(", content)
        if not m:
            return False
        method = m.group(1)
        readonly_prefixes = ("get", "is", "has", "size", "length", "toString", "hashCode")
        return method.startswith(readonly_prefixes)

    for unit in units:
        is_branch = unit.get('is_branch', False)
        branch_condition = unit.get('branch_condition', '')
        uses = unit.get('uses', [])
        defs = unit.get('defs', [])
        content = unit.get('content', '')
        is_throw_stmt = content.strip().startswith('throw') or ' throw ' in f" {content} "
        is_invoke_stmt = 'invoke' in content
        
        current_origins = set()
        for u in uses:
            kind = u.get('kind')
            name = u.get('name')
            if kind == 'field':
                is_static = bool(u.get('is_static', False))
                prefix = "STATIC_FIELD_" if is_static else "FIELD_"
                origin = f"{prefix}{name}"
                current_origins.add(origin)
                add_metric(origin, 'reads')
                if is_branch:
                    add_metric(origin, 'branch_usage')
                    # If we have a branch condition string, check if this variable name is in it
                    # Note: Jimple conditions like "if $i0 > 10" will have the local name
                    if branch_condition:
                        add_metric(origin, 'path_constraints', detail=branch_condition)
                        if name in branch_condition:
                            add_metric(origin, 'branch_condition_usage', amount=1)
            elif kind == 'local':
                if name in var_origins:
                    origins = var_origins[name]
                    current_origins.update(origins)
                    if is_branch:
                        for origin in origins:
                            add_metric(origin, 'branch_usage')
                            if branch_condition:
                                # Back-propagate constraints: assign this branch's condition string to the origin (PARAM/FIELD)
                                add_metric(origin, 'path_constraints', detail=branch_condition)
                                if name in branch_condition:
                                    add_metric(origin, 'branch_condition_usage', amount=1)
                else:
                    # In some cases, a local might not have an origin yet but it's used in a branch.
                    # This happens for fresh locals created within the method. We only care about tracking back to PARAM/FIELD.
                    pass

        if is_branch:
            flow_summary["branch_sites"].append({
                "unit_id": unit.get("id"),
                "line": unit.get("line"),
                "condition": branch_condition,
                "origins": sorted(current_origins),
            })

        if content.strip().startswith("return"):
            flow_summary["return_sites"].append({
                "unit_id": unit.get("id"),
                "line": unit.get("line"),
                "content": content,
                "origins": sorted(current_origins),
                "returns_constant": any(u.get("kind") == "constant" for u in uses),
                "uses": [
                    {
                        "kind": u.get("kind"),
                        "name": u.get("name"),
                        "type": u.get("type"),
                        "string": u.get("string"),
                    }
                    for u in uses
                ],
            })

        # Capture both explicit throw and potential implicit null-deref triggers (invoke and field reads usually throw if null)
        is_implicit_exception_trigger = (is_invoke_stmt or any(u.get('kind') == 'field' for u in uses))
        
        if is_throw_stmt or is_implicit_exception_trigger:
            for origin in current_origins:
                # Give a full exception metric for explicit throws, but just a small partial weight for implicit NPE risks
                weight = 1 if is_throw_stmt else 0.2
                add_metric(origin, 'exception_usage', amount=weight)

        # Heuristic: method invocation can mutate receiver/argument object state even without explicit field defs.
        if is_invoke_stmt and not is_likely_readonly_invoke(content):
            for origin in current_origins:
                if origin == 'THIS' or origin.startswith('PARAM_'):
                    add_metric(origin, 'writes')

        for d in defs:
            kind = d.get('kind')
            name = d.get('name')
            if kind == 'other' and d.get('class') == 'JArrayRef':
                ref_str = d.get('string', '')
                if '[' in ref_str:
                    array_local = ref_str.split('[')[0].strip()
                    if array_local in var_origins:
                        for origin in var_origins[array_local]:
                            add_metric(origin, 'writes')
            if kind == 'local':
                if current_origins: 
                    var_origins[name] = current_origins.copy()
                    # If any of the origins has constraints (e.g. from an earlier modification causing a branch), 
                    # we could propagate them, but typically we want the flow to go FROM assignment TO branches.
                    # This already happens because current_origins tracks the SOURCE.
                else: 
                     if name in var_origins: del var_origins[name]
            elif kind == 'field':
                is_static = bool(d.get('is_static', False))
                prefix = "STATIC_FIELD_" if is_static else "FIELD_"
                origin = f"{prefix}{name}"
                add_metric(origin, 'writes')
                # Propagate write credit AND origins back to fields if we wanted to track deep flow.
                # For now, we propagate the fact that these origins modified the field.
                for o in current_origins:
                    add_metric(o, 'writes')
    
    # --- CROSS-VARIABLE CONSTRAINT PROPAGATION (POST-PROCESS) ---
    # Ensure that any branch condition involving a local that originated from a PARAM/FIELD
    # is correctly attributed back.
    # We already handle this in the unit loop via `for origin in origins: add_metric(origin, 'path_constraints', ...)`
    # This is "back-propagation" in real-time.
    
    # --- PDG INFLUENCE ANALYSIS ---
    pdg_data = soot_data.get('pdg', {})
    if pdg_data:
        pdg_nodes = pdg_data.get('nodes', [])
        pdg_edges = pdg_data.get('edges', [])
        
        # Build unit to PDG node map
        unit_to_pdg_ids = {}
        for node in pdg_nodes:
            for unit_id in node.get('units', []):
                if unit_id not in unit_to_pdg_ids:
                    unit_to_pdg_ids[unit_id] = []
                unit_to_pdg_ids[unit_id].append(node['id'])
        
        # Simple reachability heuristic: distance 1 dependants
        pdg_adj = {}
        for edge in pdg_edges:
            f, t = edge['from'], edge['to']
            if f not in pdg_adj: pdg_adj[f] = set()
            pdg_adj[f].add(t)
            
        for unit in units:
            u_id = unit.get('id')
            if u_id in unit_to_pdg_ids:
                p_ids = unit_to_pdg_ids[u_id]
                # Find all unique dependants of these nodes
                dependants = set()
                for p_id in p_ids:
                    dependants.update(pdg_adj.get(p_id, []))
                
                num_deps = len(dependants)
                if num_deps > 0:
                    # Credit this unit's uses with PDG influence
                    uses = unit.get('uses', [])
                    for u in uses:
                        kind, name = u.get('kind'), u.get('name')
                        if kind == 'field':
                            origin = f"{'STATIC_FIELD_' if u.get('is_static') else 'FIELD_'}{name}"
                            add_metric(origin, 'pdg_influence', amount=num_deps)
                        elif kind == 'local' and name in var_origins:
                            for origin in var_origins[name]:
                                add_metric(origin, 'pdg_influence', amount=num_deps)

    return metrics, flow_summary


def _compile_project_for_soot(project_path, changed_paths):
    """Compile a copied project before Soot analysis."""
    ret = None
    try:
        ret = optimized_compile(project_path, changed_paths)
    except Exception as exc:
        print(f"[WARN] optimized_compile failed, fallback to fast_compile.sh: {exc}")

    if ret is None or getattr(ret, "returncode", 1) != 0:
        try:
            timeout = globals().get("COMPILE_TIMEOUT", 120)
            ret = run_cmd_with_timeout(
                ["bash", "fast_compile.sh"],
                cwd=project_path,
                timeout=timeout,
            )
        except Exception as exc:
            return None, f"compile_exception:{type(exc).__name__}:{exc}"

    if ret is None:
        return None, "compile_timeout_or_no_result"
    if getattr(ret, "returncode", 1) != 0:
        return ret, ((getattr(ret, "stdout", "") or "") + "\n" + (getattr(ret, "stderr", "") or "")).strip()
    return ret, None


def _copy_project_for_mutant_static(entry):
    """Copy project into /tmp so mutant replacement and compilation do not touch source."""
    project_dir_name = f"{entry.get('bug_num', '')}_{entry.get('project', '')}"
    src_project_path = os.path.join(CWD, project_dir_name)
    if not os.path.exists(src_project_path):
        raise FileNotFoundError(f"Project not found: {src_project_path}")

    temp_dir = tempfile.mkdtemp(prefix=f"soot_mutants_{project_dir_name}_")
    project_path = os.path.join(temp_dir, project_dir_name)

    try:
        subprocess.run(["cp", "-al", src_project_path, project_path], check=True, stderr=subprocess.DEVNULL)
    except Exception:
        shutil.copytree(src_project_path, project_path)

    # Break hardlinks under target before compilation writes class files.
    src_target = os.path.join(src_project_path, "target")
    dst_target = os.path.join(project_path, "target")
    if os.path.exists(dst_target):
        shutil.rmtree(dst_target, ignore_errors=True)
        if os.path.exists(src_target):
            shutil.copytree(src_target, dst_target)

    relative_main_path = os.path.relpath(entry.get("main_method_path", ""), src_project_path)
    temp_main_method_path = os.path.join(project_path, relative_main_path)
    if not os.path.exists(temp_main_method_path):
        raise FileNotFoundError(f"Main method file not found in temp project: {temp_main_method_path}")

    # Break hardlink for the source file before replacing mutants.
    with open(temp_main_method_path, "r", encoding="utf-8") as f:
        original_content = f.read()
    os.unlink(temp_main_method_path)
    with open(temp_main_method_path, "w", encoding="utf-8") as f:
        f.write(original_content)

    return {
        "temp_dir": temp_dir,
        "project_path": project_path,
        "relative_main_path": relative_main_path,
        "temp_main_method_path": temp_main_method_path,
        "original_content": original_content,
    }


def analyze_single_mutant_variable_flow(entry, mutant, project_state=None, mutant_index=None):
    """Compile one mutant in a temp project and run Soot static analysis on it.

    If `project_state` is provided, the caller owns cleanup and restoration.
    Otherwise this function creates and deletes a temp project by itself.
    """
    owns_project_state = project_state is None
    backup_file = None
    try:
        if project_state is None:
            project_state = _copy_project_for_mutant_static(entry)

        replace_result = replace_method_in_java_file(
            project_state["temp_main_method_path"],
            entry.get("extracted_class_name", ""),
            entry.get("extracted_method_name", ""),
            mutant.get("code", ""),
        )
        backup_file = replace_result.get("backup_file")
        if not replace_result.get("success"):
            return {
                "mutant_index": mutant_index,
                "status": "replace_failed",
                "error": replace_result.get("error", "replace_method_in_java_file failed"),
            }

        compile_ret, compile_error = _compile_project_for_soot(
            project_state["project_path"],
            [project_state["relative_main_path"]],
        )
        if compile_error:
            return {
                "mutant_index": mutant_index,
                "status": "compile_failed",
                "error": compile_error,
            }

        mutant_entry = dict(entry)
        mutant_entry["_project_path_override"] = project_state["project_path"]
        mutant_entry["_classes_path_override"] = os.path.join(project_state["project_path"], "target/classes")
        mutant_entry["_static_analysis_subject"] = "mutant"
        analysis = analyze_variable_flow(mutant_entry)

        status = "ok" if isinstance(analysis, dict) and "error" not in analysis else "soot_failed"
        return {
            "mutant_index": mutant_index,
            "bug_id": mutant.get("bug_id"),
            "bug_type": mutant.get("bug_type"),
            "mutation": mutant.get("mutation"),
            "location": mutant.get("location"),
            "status": status,
            "soot_analysis_result": analysis,
        }
    except Exception as exc:
        return {
            "mutant_index": mutant_index,
            "status": f"error:{type(exc).__name__}",
            "error": str(exc),
        }
    finally:
        if backup_file and project_state and os.path.exists(project_state.get("temp_main_method_path", "")):
            restore_java_file_from_content(project_state["temp_main_method_path"], backup_file)
        if project_state and os.path.exists(project_state.get("temp_main_method_path", "")):
            try:
                with open(project_state["temp_main_method_path"], "w", encoding="utf-8") as f:
                    f.write(project_state.get("original_content", ""))
            except Exception:
                pass
        if owns_project_state and project_state and os.path.exists(project_state.get("temp_dir", "")):
            shutil.rmtree(project_state["temp_dir"], ignore_errors=True)


def analyze_mutant_variable_flows(entry, max_mutants=None, save_into_variants=True):
    """Run Soot static analysis for AST-generated mutants in `entry`.

    Returns a list of per-mutant records.  Each successful record contains
    `soot_analysis_result` with the same schema as `analyze_variable_flow`.
    """
    mutants = entry.get("ast_generates", []) or []
    max_mutants = len(mutants)
    if max_mutants is not None:
        mutants = mutants[:max(0, int(max_mutants))]

    project_state = None
    results = []
    try:
        project_state = _copy_project_for_mutant_static(entry)
        for mutant_index, mutant in enumerate(mutants):
            if not isinstance(mutant, dict):
                results.append({
                    "mutant_index": mutant_index,
                    "status": "invalid_mutant_record",
                    "error": "mutant is not a dict",
                })
                continue
            result = analyze_single_mutant_variable_flow(
                entry,
                mutant,
                project_state=project_state,
                mutant_index=mutant_index,
            )
            results.append(result)
            if save_into_variants:
                mutant["mutant_static_analysis"] = result
    finally:
        if project_state and os.path.exists(project_state.get("temp_dir", "")):
            shutil.rmtree(project_state["temp_dir"], ignore_errors=True)
    return results


def summarize_mutant_static_analyses(mutant_static_results):
    summary = {
        "total_mutants": len(mutant_static_results or []),
        "status_counts": {},
        "ok_count": 0,
        "avg_return_sites": 0.0,
        "avg_branch_sites": 0.0,
        "avg_field_count": 0.0,
    }
    return_sites = []
    branch_sites = []
    field_counts = []
    for result in mutant_static_results or []:
        status = result.get("status", "unknown")
        summary["status_counts"][status] = summary["status_counts"].get(status, 0) + 1
        if status != "ok":
            continue
        summary["ok_count"] += 1
        soot = result.get("soot_analysis_result") or {}
        return_sites.append((soot.get("return_flow") or {}).get("return_site_count", 0))
        branch_sites.append(len(soot.get("branch_sites") or []))
        fields = soot.get("field_metrics") or {}
        field_counts.append(len(fields.get("instance") or {}) + len(fields.get("static") or {}))
    if summary["ok_count"]:
        summary["avg_return_sites"] = sum(return_sites) / summary["ok_count"]
        summary["avg_branch_sites"] = sum(branch_sites) / summary["ok_count"]
        summary["avg_field_count"] = sum(field_counts) / summary["ok_count"]
    return summary


def _parse_args():
    parser = argparse.ArgumentParser(description="Run Soot static analysis on original methods and/or AST mutants.")
    parser.add_argument("--input-json", help="Input JSON list.")
    parser.add_argument("--output-json", help="Output JSON path.")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-mutants", type=int, default=None)
    parser.add_argument("--skip-original", action="store_true", help="Do not compute missing original soot_analysis_result.")
    parser.add_argument("--mutants", action="store_true", help="Analyze ast_generates mutants.")
    parser.add_argument("--save-every", type=int, default=10)
    return parser.parse_args()


def _main():
    args = _parse_args()
    if not args.input_json or not args.output_json:
        raise SystemExit("--input-json and --output-json are required")

    with open(args.input_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    end = len(data) if args.limit is None else min(len(data), args.start + args.limit)
    for item_index in range(args.start, end):
        item = data[item_index]
        print(f"[{item_index + 1}/{len(data)}] {item.get('bug_num')}_{item.get('project')} {item.get('test_name')}")
        if not args.skip_original and "soot_analysis_result" not in item:
            item["soot_analysis_result"] = analyze_variable_flow(item)
        if args.mutants:
            results = analyze_mutant_variable_flows(item, max_mutants=args.max_mutants, save_into_variants=True)
            item["mutant_static_analysis_summary"] = summarize_mutant_static_analyses(results)
            print(f"  mutant static: {item['mutant_static_analysis_summary']}")
        if args.save_every and (item_index + 1) % args.save_every == 0:
            with open(args.output_json, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved: {args.output_json}")


if __name__ == "__main__":
    _main()
