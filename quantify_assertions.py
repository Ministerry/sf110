import json

data = {
    "soot_analysis_result": [
      {
        "variable": "dBPrimaryKeyConstraint0",
        "role": "Focal Object (State Owner)",
        "control_flow_influence": {
          "branch_decisions_dependent_on_state": 2
        },
        "data_flow_state_access": {
          "fields_read": 4,
          "fields_modified": 0,
          "fields_list": [
            "name",
            "columnNames"
          ]
        }
      },
      {
        "variable": "stringArray0",
        "role": "Constructor Argument (State Initializer)",
        "flow_influence": {
          "initializes_state_used_in_branches": 2,
          "backing_data_read": 4,
          "backing_data_modified": 0
        }
      },
      {
        "variable": "boolean0",
        "role": "Return Value (Output)",
        "flow_sources": "Result of computation"
      }
    ],
    "dynamic_analysis": {
      "before": {}, 
      "after": {
        "defaultDBTable0": {
          "fields": {},
          "elements": {}
        },
        "stringArray0": {
            "fields": {},
            "elements": { "0": "null" }
        },
        "dBPrimaryKeyConstraint0": {
            "fields": { "name": "..." },
            "elements": {}
        },
        "boolean0": {
          "value": "false",
          "fields": {},
          "elements": {}
        }
      },
      "diff": {
        "boolean0": {
          "type": "new",
          "changes": [],
          "value": "false"
        }
      }
    }
}

def quantify_assertion_value(soot_data, dynamic_data):
    results = {}
    
    # Analyze variables present in Soot analysis
    for soot_var in soot_data:
        var_name = soot_var["variable"]
        score_breakdown = {
            "Role": 0,
            "Modification": 0,
            "Impact": 0,
            "Complexity": 0
        }
        
        # 1. Role Score
        role = soot_var.get("role", "")
        if "Return Value" in role:
            score_breakdown["Role"] = 10
        elif "Focal Object" in role:
            score_breakdown["Role"] = 8
        elif "Constructor Argument" in role:
            score_breakdown["Role"] = 3
        else:
            score_breakdown["Role"] = 1
            
        # 2. Modification Score
        # Check Dynamic Diff
        if var_name in dynamic_data.get("diff", {}):
            diff_info = dynamic_data["diff"][var_name]
            if diff_info.get("type") == "new":
                score_breakdown["Modification"] = 10 # Created new object/value
            else:
                 score_breakdown["Modification"] = 10 # Modified existing
        
        # Check Static Modification if dynamic missed it (or confirm it)
        # Static modification is a strong indicator even if dynamic diff is empty (null op)
        fields_modified = soot_var.get("data_flow_state_access", {}).get("fields_modified", 0)
        if fields_modified > 0:
            score_breakdown["Modification"] = max(score_breakdown["Modification"], 8)

        # 3. Impact Score (Control Flow)
        # How critical is this variable to the logic?
        cf_influence = soot_var.get("control_flow_influence", {})
        branches = cf_influence.get("branch_decisions_dependent_on_state", 0)
        
        flow_influence = soot_var.get("flow_influence", {})
        branches_init = flow_influence.get("initializes_state_used_in_branches", 0)
        
        total_branches = branches + branches_init
        score_breakdown["Impact"] = min(total_branches * 2, 10) # Cap at 10
        
        # 4. Complexity/Deep Score (Structure)
        # Is there internal structure worth asserting?
        after_state = dynamic_data.get("after", {}).get(var_name, {})
        fields = after_state.get("fields", {})
        elements = after_state.get("elements", {})
        
        if len(fields) > 0:
            score_breakdown["Complexity"] += 2
        if len(elements) > 0:
            score_breakdown["Complexity"] += 3
            
        # Soot fields list hint
        static_fields_list = soot_var.get("data_flow_state_access", {}).get("fields_list", [])
        if len(static_fields_list) > 0:
             score_breakdown["Complexity"] += 2

        # Final Sum
        total_score = sum(score_breakdown.values())
        results[var_name] = {
            "total_score": total_score,
            "breakdown": score_breakdown,
            "recommendation": get_recommendation(var_name, score_breakdown, role)
        }
        
    return results

def get_recommendation(var_name, scores, role):
    recs = []
    if "Return Value" in role:
        recs.append("MUST: Assert return value (assertEquals/True/False).")
    
    if scores["Modification"] > 5 and "Return Value" not in role:
        recs.append("MUST: Assert state change (fields changed).")
        
    if scores["Impact"] > 0 and scores["Modification"] == 0:
        recs.append("SHOULD: Assert invariant (state used in logic maintained).")
        
    if scores["Complexity"] >= 4:
        recs.append(f"hint: Deep assertion needed (check fields/elements of {var_name}).")
        
    if not recs:
        recs.append("LOW: Low priority for assertion.")
        
    return " ".join(recs)

scores = quantify_assertion_value(data["soot_analysis_result"], data["dynamic_analysis"])

print(f"{'Variable':<25} | {'Total':<5} | {'Role':<5} | {'Mod':<5} | {'Imp':<5} | {'Cplx':<5} | Recommendation")
print("-" * 100)
for var, info in scores.items():
    bd = info['breakdown']
    print(f"{var:<25} | {info['total_score']:<5} | {bd['Role']:<5} | {bd['Modification']:<5} | {bd['Impact']:<5} | {bd['Complexity']:<5} | {info['recommendation']}")
