import re

with open('/home/ubuntu/myren/SF110/rewards_train_utils_no_dyn.py', 'r') as f:
    text = f.read()

# Replace weights
text = re.sub(r'\s*"role_return_no_side_effects":.*?,\n', '\n    "role_return": 30.0,\n', text)
text = re.sub(r'\s*# Full-set tuning.*?\n', '', text)
text = re.sub(r'\s*# emphasis when combined.*?\n', '', text)
text = re.sub(r'\s*"role_return_with_side_effects":.*?,\n', '\n', text)

# Rewrite has_side_effects logic in quantify_assertion_value
# Replace `has_side_effects = len(dynamic_diff) > 0` with static side effects
static_side_effects_logic = """    # Check for global side effects statically via Soot
    has_side_effects = False
    for v in soot_data:
        if isinstance(v, dict):
            df = v.get("data_flow_state_access", {})
            if isinstance(df, dict) and df.get("fields_modified", 0) > 0:
                has_side_effects = True
                break"""
text = re.sub(r'\s*# Check for global side effects \(any variable modified\)\n\s*has_side_effects = len\(dynamic_diff\) > 0', static_side_effects_logic, text)

# Replace role_return_no_side_effects usage
text = re.sub(r'weights\["role_return_no_side_effects"\] if not has_side_effects else weights\["role_return_with_side_effects"\]', 'weights["role_return"]', text)

with open('/home/ubuntu/myren/SF110/rewards_train_utils_no_dyn.py', 'w') as f:
    f.write(text)
