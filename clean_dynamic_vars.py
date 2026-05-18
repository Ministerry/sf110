import re

with open('/home/ubuntu/myren/SF110/rewards_train_utils_no_dyn.py', 'r') as f:
    text = f.read()

# Fix signatures
text = re.sub(r'def quantify_assertion_value\(soot_data_wrapped, dynamic_data\):', 'def quantify_assertion_value(soot_data_wrapped):', text)
text = re.sub(r'def _extract_assertion_entities\(assertion, soot_data_wrapped, dynamic_data\):', 'def _extract_assertion_entities(assertion, soot_data_wrapped):', text)
text = re.sub(r'def _diagnose_variable_matching\(var_names, var_scores, soot_data_wrapped, dynamic_data, extraction_debug=None\):', 'def _diagnose_variable_matching(var_names, var_scores, soot_data_wrapped, extraction_debug=None):', text)
text = re.sub(r'def _compute_semantic_outputs\(assertion, soot_data_wrapped, dynamic_data, total_score\):', 'def _compute_semantic_outputs(assertion, soot_data_wrapped, total_score):', text)
text = re.sub(r'def score_assertion_from_statement\(assertion: str, soot_data_wrapped, dynamic_data\):', 'def score_assertion_from_statement(assertion: str, soot_data_wrapped):', text)

# Now, we should also replace any dynamic_data logic inside the functions.
# Let's just define `dynamic_data = {}` at the top of these functions to avoid undefined variable errors,
# and let the existing safety checks ignore them, or we can strip them completely.
# Since ripping out AST logic manually with regex is flaky, injecting `dynamic_data = {}` and removing it from signature is safer.

with open('/home/ubuntu/myren/SF110/rewards_train_utils_no_dyn.py', 'w') as f:
    f.write(text)

