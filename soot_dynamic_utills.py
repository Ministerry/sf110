import os
import json
import pandas as pd
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

def analyze_variable_flow(entry):
    project_dir_name = f"{entry['bug_num']}_{entry['project']}"
    project_classes_path = os.path.join(CWD, project_dir_name, "target/classes")
    
    # Try finding classes path
    if not os.path.exists(project_classes_path):
        base_path = os.path.join(CWD, project_dir_name)
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
            
        metrics = calculate_metrics_from_soot(soot_data)
        
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
                "static_fields_read": static_field_metrics['reads'],
                "static_fields_modified": static_field_metrics['writes'],
                "static_fields_list": accessed_static_fields
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
                "flow_sources": "Result of computation"
            })
            
        return {
            "variables": results,
            "global_metrics": global_soot_info
        }

    except Exception as e:
        if os.path.exists(output_file):
            os.remove(output_file)
        return {"error": str(e)}

def calculate_metrics_from_soot(soot_data):
    if not soot_data: return {}
    units = soot_data.get('units', [])
    metrics = {}
    local_role_map = {} 
    
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

    return metrics

def get_method_invocation_split(code, method_name):
    try:
        prefix_lines = "public class Wrapper {\n  public void test() {\n"
        wrapped_multiline = prefix_lines + code + "\n  }\n}"
        
        tokens_2 = javalang.tokenizer.tokenize(wrapped_multiline)
        parser_2 = javalang.parser.Parser(tokens_2)
        tree_2 = parser_2.parse()
        
        target_node_2 = None
        target_call_2 = None
        target_candidates = []

        # 1. Try MethodInvocation (collect all candidates; choose the last occurrence)
        for path, node in tree_2.filter(javalang.tree.MethodInvocation):
            if node.member == method_name:
                parent_stmt = None
                for parent in reversed(path):
                    if isinstance(parent, (javalang.tree.Statement, javalang.tree.LocalVariableDeclaration)):
                        parent_stmt = parent
                        break
                if parent_stmt is not None:
                    target_candidates.append((parent_stmt, node))

        # 2. Try ClassCreator only when no invocation candidate exists
        if not target_candidates:
            for path, node in tree_2.filter(javalang.tree.ClassCreator):
                if node.type.name == method_name:
                    parent_stmt = None
                    for parent in reversed(path):
                        if isinstance(parent, (javalang.tree.Statement, javalang.tree.LocalVariableDeclaration)):
                            parent_stmt = parent
                            break
                    if parent_stmt is not None:
                        target_candidates.append((parent_stmt, node))

        if target_candidates:
            # Prefer the last call in source order, which is usually the focal invocation.
            def _pos_key(item):
                parent_stmt = item[0]
                if parent_stmt and parent_stmt.position:
                    return (parent_stmt.position[0], parent_stmt.position[1])
                return (-1, -1)

            target_node_2, target_call_2 = sorted(target_candidates, key=_pos_key)[-1]

        vars_all = []
        vars_before = []
        
        limit_line = 999999
        limit_col = 999999
        if target_node_2 and target_node_2.position:
            limit_line, limit_col = target_node_2.position
            
        for path, node in tree_2.filter(javalang.tree.LocalVariableDeclaration):
            type_name = "Object" # Default
            try:
                type_name = node.type.name
                if hasattr(node.type, 'dimensions') and node.type.dimensions:
                    type_name += '[]' * len(node.type.dimensions)
            except: pass

            for decl in node.declarators:
                vars_all.append((decl.name, type_name))
                if node.position:
                    line, col = node.position
                    if line < limit_line:
                        vars_before.append((decl.name, type_name))
                    elif line == limit_line and col < limit_col: 
                         vars_before.append((decl.name, type_name))

        # Add likely relevant non-local variables from the focal invocation itself.
        # This captures cases where arguments/qualifier are fields or otherwise not locally declared.
        invocation_refs = []
        if isinstance(target_call_2, javalang.tree.MethodInvocation):
            qualifier = getattr(target_call_2, 'qualifier', None)
            # Only include qualifier if it looks like a variable (starts with lowercase or underscore) or is 'this'
            if qualifier and re.match(r'^[A-Za-z_$][A-Za-z0-9_$]*$', qualifier):
                if qualifier == 'this' or qualifier[0].islower() or qualifier[0] == '_':
                    invocation_refs.append(qualifier)
            for arg in getattr(target_call_2, 'arguments', []) or []:
                if isinstance(arg, javalang.tree.MemberReference):
                    mem = arg.member
                    if mem and (mem == 'this' or mem[0].islower() or mem[0] == '_'):
                        invocation_refs.append(mem)

        seen_names = {name for name, _ in vars_all}
        for ref_name in invocation_refs:
            if ref_name not in seen_names:
                vars_all.append((ref_name, "Object"))
                vars_before.append((ref_name, "Object"))
                seen_names.add(ref_name)

        if not target_node_2 or not target_node_2.position: return None, None, [], vars_all
        
        start_line_2, start_col_2 = target_node_2.position
        code_start_line_index = start_line_2 - 3 
        
        if code_start_line_index < 0: return None, None, [], vars_all
        
        code_lines = code.splitlines(keepends=True)
        if code_start_line_index >= len(code_lines): return None, None, [], vars_all
        
        offset = 0
        for i in range(code_start_line_index):
            offset += len(code_lines[i])
        offset += (start_col_2 - 1)
        
        end_offset = offset
        in_string = False
        parens = 0
        while end_offset < len(code):
            c = code[end_offset]
            if c == '"' and (end_offset == 0 or code[end_offset-1] != '\\'): in_string = not in_string
            if not in_string:
                if c == '(': parens+=1
                elif c == ')': parens-=1
                elif c == ';' and parens == 0:
                    end_offset += 1
                    break
            end_offset += 1
            
        return offset, end_offset, vars_before, vars_all
    except:
        return None, None, [], []

# def generate_dump_block(label, var_infos):
#     if not var_infos: return ""
#     code = f'        java.lang.System.out.println("___DYNAMIC_{label}___");\n'
#     code += '        try {\n'
#     code += '            java.util.Set<Integer> visitedObjs = new java.util.HashSet<>();\n'
    
#     for var, vtype in var_infos:
#         is_primitive = vtype in ['int', 'long', 'boolean', 'char', 'float', 'double', 'short', 'byte']
        
#         if is_primitive:
#              code += f'            java.lang.System.out.println("VAR_VALUE:{var}=" + {var});\n'
#         else:
#             # Main object dump
#             code += f'            if ({var} != null) {{ '
#             code += f' java.lang.System.out.println("VAR_VALUE:{var}=" + {var});'
#             code += f' int id = java.lang.System.identityHashCode({var});'
#             code += f' if (!visitedObjs.contains(id)) {{ visitedObjs.add(id);'
            
#             code += f' Class<?> c = {var}.getClass();'
#             # Array handling
#             code += f' if(c.isArray()){{ int l=java.lang.reflect.Array.getLength({var});java.lang.System.out.println("VAR_ARRAY_LEN:{var}="+l);'
#             code += f' if(l>0){{for(int i=0;i<Math.min(l,20);i++){{'
#             code += f' Object elem=java.lang.reflect.Array.get({var},i); java.lang.System.out.println("VAR_ARRAY_ELEM:{var}:"+i+"="+elem);'
#             code += f' if(elem!=null && !elem.getClass().isPrimitive() && !elem.getClass().getName().startsWith("java.lang.")) {{ int elemId=java.lang.System.identityHashCode(elem); if(!visitedObjs.contains(elemId)){{ visitedObjs.add(elemId); try {{ for(java.lang.reflect.Field ff:elem.getClass().getDeclaredFields()){{ ff.setAccessible(true); java.lang.System.out.println("VAR_DEEP_FIELD:{var}:["+i+"]."+ff.getName()+"="+ff.get(elem)); }} }}catch(Exception e){{}} }} }}'
#             code += f' }}}} }}'
#             # Collection handling
#             code += f' else if(java.util.Collection.class.isAssignableFrom(c)) {{'
#             code += f'     java.util.Collection<?> col=(java.util.Collection<?>)(Object){var}; java.lang.System.out.println("VAR_COLL_SIZE:{var}="+col.size());'
#             code += f'     int _idx=0; for(Object _elem : col) {{ if(_idx>=20) break; java.lang.System.out.println("VAR_ARRAY_ELEM:{var}:"+(_idx++)+"="+_elem); }}'
#             code += f' }}'
#             # Map handling
#             code += f' else if(java.util.Map.class.isAssignableFrom(c)) {{'
#             code += f'     java.util.Map<?,?> map=(java.util.Map<?,?>)(Object){var}; java.lang.System.out.println("VAR_MAP_SIZE:{var}="+map.size());'
#             code += f'     int _idx=0; for(java.util.Map.Entry<?,?> _entry : map.entrySet()) {{ if(_idx>=20) break; java.lang.System.out.println("VAR_ARRAY_ELEM:{var}:"+(_idx++)+"="+_entry.getKey()+"->"+_entry.getValue()); }}'
#             code += f' }}'
#             # Object handling - NOT an else if, so collections still get their underlying structure dumped or size printed.
#             code += f' if(!c.isPrimitive() && !c.isArray() && !c.getName().startsWith("java.lang.") && !(c.getName().equals("java.lang.String"))){{'
#             code += f' Class<?> cc=c; while(cc!=null && !cc.getName().startsWith("java.lang.Object")){{'
#             code += f' java.lang.reflect.Field[] fs=cc.getDeclaredFields(); for(java.lang.reflect.Field f:fs){{'
#             code += f' f.setAccessible(true); Object val = null; try{{val=f.get({var});}}catch(Exception e){{continue;}}'
            
#             # Getter Probe Logic (Safe invocation without side-effects)
#             code += f' String n = f.getName();'
#             code += f' String items[] = {{ "get" + Character.toUpperCase(n.charAt(0)) + n.substring(1), "is" + Character.toUpperCase(n.charAt(0)) + n.substring(1), "has" + Character.toUpperCase(n.charAt(0)) + n.substring(1) }};'
#             code += f' Object getterVal = null;'
#             code += f' String getterName = null;'
#             code += f' for(String g : items) {{ try {{ java.lang.reflect.Method m = c.getMethod(g); '
#             # Check if it returns same type and takes NO arguments (0 args), and is not a well-known side-effecting method.
#             code += f' if(m.getReturnType().isAssignableFrom(f.getType()) && m.getParameterCount() == 0 && !g.equals("iterator") && !g.equals("listIterator") && !g.equals("elements")) {{ '
#             code += f'   Object gVal = m.invoke({var}); getterVal = gVal; if(gVal==val || (val!=null && val.equals(gVal))) {{ getterName=g; try {{ java.lang.System.out.println("VAR_GETTER:{var}:"+getterName+"="+(gVal==null?"null":gVal)); }} catch(Throwable t){{}} break; }} '
#             code += f' }} }} catch(Exception e){{}} }}'
#             code += f' java.lang.System.out.println("VAR_FIELD:{var}:"+n+"=" + val + (getterName!=null ? ("__ACCESSIBLE:"+getterName) : ""));'
#             code += f' if(getterName!=null) {{ try {{ java.lang.System.out.println("VAR_GETTER:{var}:"+getterName+"="+(getterVal==null?"null":getterVal)); }} catch(Throwable t){{}} }}'

#             # 1-Level Deep Dump for complex fields
#             code += f' if(val!=null && !f.getType().isPrimitive() && !f.getType().getName().startsWith("java.lang.")) {{'
#             code += f'   int valId=java.lang.System.identityHashCode(val); if(!visitedObjs.contains(valId)){{ visitedObjs.add(valId); try {{'
#             code += f'     Class<?> fc = val.getClass();'
#             code += f'     if(!fc.isArray()) {{'
#             code += f'       java.lang.reflect.Field[] ffs=fc.getDeclaredFields();'
#             code += f'       for(java.lang.reflect.Field ff:ffs) {{ ff.setAccessible(true); java.lang.System.out.println("VAR_DEEP_FIELD:{var}:"+n+"."+ff.getName()+"="+ff.get(val)); }}'
#             code += f'     }}'
#             code += f'   }} catch(Exception e) {{}} }}'
#             code += f' }}'

#             code += f' }} cc=cc.getSuperclass(); }}'
#             code += f' }}' 
            
#             code += f' }} }} else {{ java.lang.System.out.println("VAR_VALUE:{var}=null"); }}\n'
            
#     code += '        } catch (Throwable t) { t.printStackTrace(); }\n'
#     code += f'        java.lang.System.out.println("___DYNAMIC_{label}_END___");\n'
#     return code

# def perform_dynamic_analysis(entry, project_path, relative_main_path, relative_test_path):
#     """
#     Injects instrumentation code into the test case to capture runtime values of variables.
#     """
#     try:
#         prefix = entry['prefix']
#         extracted_method_name = entry['extracted_method_name']
#         test_code_body = prefix
        
#         # 1. Parse to find variables
#         # Calculate split logic
#         split_start, split_end, vars_before, vars_all = get_method_invocation_split(prefix, extracted_method_name)
        
#         if not vars_all:
#              return {"before": {}, "after": {}, "diff": {}, "warning": "No variables found in prefix"}

#         # 2. Construct Instrumented Body
#         if split_start is not None and split_end is not None:
#             code_before = generate_dump_block("BEFORE", vars_before)
#             code_after = generate_dump_block("AFTER", vars_all)
#             test_code_body = prefix[:split_start] + "\n" + code_before + "\n" + prefix[split_start:] + "\n" + code_after
#         else:
#             # Fallback: Just After
#             code_after = generate_dump_block("AFTER", vars_all)
#             test_code_body = prefix + "\n" + code_after

#         # 3. Create Test Method
#         test_code = "public void testDynamicAnalysis() throws Throwable { \n " + test_code_body + "\n}"
        
#         # 4. Write and Run (Standard logic)
#         temp_test_path = os.path.join(project_path, relative_test_path)
#         test_name = entry['test_name']
#         bug_num = entry['bug_num']
#         project = entry['project']
        
#         replace_from_first_brace(temp_test_path, test_code, f"{bug_num}_{project}")
#         compile_ret = run_cmd_with_timeout(['bash', 'fast_compile.sh'], cwd=project_path, timeout=60)
        
#         if compile_ret.returncode != 0:
#              print(f"[ERROR-COMPILE] STDOUT: {compile_ret.stdout}")
#              print(f"[ERROR-COMPILE] STDERR: {compile_ret.stderr}")
#              return {"error": "Dynamic analysis compilation failed"}
             
#         test_class = test_name + 'EvoSuiteTest'
#         run_ret = run_cmd_with_timeout(['bash', 'run.sh', test_class], cwd=project_path, timeout=RUN_TIMEOUT)
        
#         if not run_ret:
#             return {"error": "Dynamic analysis execution timeout/failed"}
            
#         output = (run_ret.stdout or "") + "\n" + (run_ret.stderr or "")
        
#         # 5. Parse Output (New Format)
#         dynamic_data = {'before': {}, 'after': {}}
#         current_phase = None
        
#         for line in output.splitlines():
#             if "___DYNAMIC_BEFORE___" in line:
#                 current_phase = 'before'
#                 continue
#             if "___DYNAMIC_AFTER___" in line:
#                 current_phase = 'after'
#                 continue
#             if "_END___" in line:
#                 current_phase = None
#                 continue
            
#             if current_phase:
#                 target_dict = dynamic_data[current_phase]
#                 if line.startswith("VAR_VALUE:"):
#                     parts = line.split("=", 1)
#                     if len(parts) == 2:
#                         var_key = parts[0].split(":")[1]
#                         val = parts[1]
#                         if var_key not in target_dict: 
#                             target_dict[var_key] = {
#                                 'value': val,
#                                 'fields': {},
#                                 'elements': {},
#                                 'deep_fields': {},
#                                 'array_len': None
#                             }
#                         target_dict[var_key]['value'] = val
#                 elif line.startswith("VAR_ARRAY_LEN:"):
#                     parts = line.split("=", 1)
#                     if len(parts) == 2:
#                         meta = parts[0].split(":")
#                         if len(meta) >= 2:
#                             var_key = meta[1]
#                             arr_len = parts[1]
#                             if var_key not in target_dict:
#                                 target_dict[var_key] = {
#                                     'value': None,
#                                     'fields': {},
#                                     'elements': {},
#                                     'deep_fields': {},
#                                     'array_len': None
#                                 }
#                             target_dict[var_key]['array_len'] = arr_len
                        
#                 elif line.startswith("VAR_FIELD:"):
#                     parts = line.split("=", 1)
#                     if len(parts) == 2:
#                         meta = parts[0].split(":") 
#                         if len(meta) >= 3:
#                             var_key = meta[1]
#                             field_name = meta[2]
#                             val = parts[1]
                            
#                             getter = None
#                             if "__ACCESSIBLE:" in val:
#                                 val, getter = val.split("__ACCESSIBLE:", 1)
                            
#                             if var_key in target_dict:
#                                 target_dict[var_key]['fields'][field_name] = {'value': val, 'getter': getter}
                
#                 elif line.startswith("VAR_GETTER:"):
#                     # Format: VAR_GETTER:{var}:{getter}={value}
#                     try:
#                         rest = line[len("VAR_GETTER:"):]
#                         var_part, rest2 = rest.split(":", 1)
#                         getter_name, getter_val = rest2.split("=", 1)
#                         if var_part not in target_dict:
#                             target_dict[var_part] = {
#                                 'value': None,
#                                 'fields': {},
#                                 'elements': {},
#                                 'deep_fields': {},
#                                 'array_len': None,
#                                 'getters': {}
#                             }
#                         entry = target_dict[var_part]
#                         entry.setdefault('getters', {})[getter_name] = getter_val
#                     except Exception:
#                         pass
                
#                 elif line.startswith("VAR_DEEP_FIELD:"):
#                     parts = line.split("=", 1)
#                     if len(parts) == 2:
#                         meta = parts[0].split(":")
#                         if len(meta) >= 3:
#                             var_key = meta[1]
#                             deep_key = meta[2] # e.g. "field1.subfield2"
#                             val = parts[1]
#                             if var_key in target_dict:
#                                 target_dict[var_key]['deep_fields'][deep_key] = val

#                 elif line.startswith("VAR_ARRAY_ELEM:"):
#                     parts = line.split("=", 1)
#                     if len(parts) == 2:
#                         meta = parts[0].split(":") 
#                         if len(meta) >= 3:
#                             var_key = meta[1]
#                             index = meta[2]
#                             val = parts[1]
#                             if var_key in target_dict:
#                                  target_dict[var_key]['elements'][index] = val

#                 elif line.startswith("VAR_COLL_SIZE:") or line.startswith("VAR_MAP_SIZE:"):
#                     parts = line.split("=", 1)
#                     if len(parts) == 2:
#                         meta = parts[0].split(":")
#                         if len(meta) >= 2:
#                             var_key = meta[1]
#                             val = parts[1]
#                             if var_key in target_dict:
#                                 target_dict[var_key]['collection_size'] = val
        
#         # 6. Compute Diff
#         diff = {}
#         all_vars = set(dynamic_data['before'].keys()) | set(dynamic_data['after'].keys())
#         for var in all_vars:
#             state_before = dynamic_data['before'].get(var)
#             state_after = dynamic_data['after'].get(var)
            
#             var_diff = {'type': 'unchanged', 'changes': []}
            
#             if state_before is None and state_after is not None:
#                 var_diff['type'] = 'new'
#                 var_diff['value'] = state_after.get('value')
#             elif state_after is None and state_before is not None:
#                 var_diff['type'] = 'removed'
#                 var_diff['value'] = state_before.get('value')
#             else:
#                 val_b = state_before.get('value')
#                 val_a = state_after.get('value')
#                 if val_b != val_a:
#                     var_diff['type'] = 'modified'
#                     var_diff['value_change'] = {'from': val_b, 'to': val_a}

#                 len_b = state_before.get('array_len')
#                 len_a = state_after.get('array_len')
#                 if (len_b is not None or len_a is not None) and len_b != len_a:
#                     var_diff['type'] = 'modified'
#                     var_diff['array_length_change'] = {'from': len_b, 'to': len_a}

#                 col_len_b = state_before.get('collection_size')
#                 col_len_a = state_after.get('collection_size')
#                 if (col_len_b is not None or col_len_a is not None) and col_len_b != col_len_a:
#                     var_diff['type'] = 'modified'
#                     var_diff['collection_size_change'] = {'from': col_len_b, 'to': col_len_a}
                
#                 fields_b = state_before.get('fields', {})
#                 fields_a = state_after.get('fields', {})
#                 field_changes = {}
#                 for f in (set(fields_a.keys()) | set(fields_b.keys())):
#                     f_struct_a = fields_a.get(f, {})
#                     f_struct_b = fields_b.get(f, {})
#                     f_val_a = f_struct_a.get('value')
#                     f_val_b = f_struct_b.get('value')

#                     if f_val_b != f_val_a:
#                          field_changes[f] = {
#                              'from': f_val_b,
#                              'to': f_val_a,
#                              'getter': f_struct_a.get('getter') or f_struct_b.get('getter')
#                          }
                
#                 if field_changes:
#                     var_diff['type'] = 'modified'
#                     var_diff['field_changes'] = field_changes
                
#                 deep_b = state_before.get('deep_fields', {})
#                 deep_a = state_after.get('deep_fields', {})
#                 deep_changes = {}
#                 for k in (set(deep_a.keys()) | set(deep_b.keys())):
#                     v_a = deep_a.get(k)
#                     v_b = deep_b.get(k)
#                     if v_b != v_a:
#                         deep_changes[k] = {'from': v_b, 'to': v_a}
#                 if deep_changes:
#                      var_diff['type'] = 'modified'
#                      var_diff['deep_changes'] = deep_changes

#                 elems_b = state_before.get('elements', {})
#                 elems_a = state_after.get('elements', {})
#                 elem_changes = {}
#                 for idx in (set(elems_a.keys()) | set(elems_b.keys())):
#                     e_val_a = elems_a.get(idx)
#                     e_val_b = elems_b.get(idx)
#                     if e_val_b != e_val_a:
#                         elem_changes[idx] = {'from': e_val_b, 'to': e_val_a}
                
#                 if elem_changes:
#                      var_diff['type'] = 'modified'
#                      var_diff['element_changes'] = elem_changes
                     
#             if var_diff['type'] != 'unchanged':
#                 diff[var] = var_diff
                
#         dynamic_data['diff'] = diff
#         return dynamic_data
        
#     except Exception as e:
#         return {"error": str(e)}
