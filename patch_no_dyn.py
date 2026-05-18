import re

with open('/home/ubuntu/myren/SF110/rewards_train_utils_no_dyn.py', 'r') as f:
    text = f.read()

# Remove 'dynamic_data=' from function defs and calls
# e.g. def quantify_assertion_value(soot_data_wrapped, dynamic_data):
text = re.sub(r',\s*dynamic_data=None', '', text)
text = re.sub(r',\s*dynamic_data', '', text)

# Remove dynamic_diff=None from get_recommendation
text = re.sub(r',\s*dynamic_diff=None', '', text)

# Inside get_recommendation:
# Remove dynamic_diff blocks
text = re.sub(r'if not isinstance\(dynamic_diff, dict\):\n\s*dynamic_diff = \{\}\n\s*field_changes = dynamic_diff.get\("field_changes", \{\}\)\n\s*deep_changes = dynamic_diff.get\("deep_changes", \{\}\)\n\s*element_changes = dynamic_diff.get\("element_changes", \{\}\)\n\s*value_change = \(dynamic_diff or \{\}\)\.get\("value_change"\) if dynamic_diff else None\n\s*array_length_change = \(dynamic_diff or \{\}\)\.get\("array_length_change"\) if dynamic_diff else None\n\s*collection_size_change = \(dynamic_diff or \{\}\)\.get\("collection_size_change"\) if dynamic_diff else None\n', '', text, flags=re.MULTILINE)

# Just overwrite the method signature completely:
text = re.sub(r'def get_recommendation\(var_name, scores, role, soot_var=None\):', 'def get_recommendation(var_name, scores, role, soot_var=None):', text)

with open('/home/ubuntu/myren/SF110/rewards_train_utils_no_dyn.py', 'w') as f:
    f.write(text)
