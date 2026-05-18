import re

with open('/home/ubuntu/myren/SF110/rewards_train_utils_no_dyn.py', 'r') as f:
    text = f.read()

# Remove the weights
removed_weights = [
    '"mod_new": 25.0', '"mod_accessible": 20.0', '"mod_field": 10.0', '"mod_none": 0.0',
    '"impact_nonmodified_multiplier": 0.2', '"bonus_return_no_side_effects": 5.0'
]
for w in removed_weights:
    text = re.sub(r'^\s*' + re.escape(w) + r',\s*\n', '', text, flags=re.MULTILINE)

# We can find def quantify_assertion_value(soot_data_wrapped): and just rewrite it or replace the dynamic part.
