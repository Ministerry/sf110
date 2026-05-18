import re

with open('/home/ubuntu/myren/SF110/rewards_train_utils_no_dyn.py', 'r') as f:
    text = f.read()

# For functions that lost their dynamic_data argument but still reference it internally, inject `dynamic_data = {}`
def inject(func_def):
    global text
    text = text.replace(func_def, func_def + '\n    dynamic_data = {}')

inject('def quantify_assertion_value(soot_data_wrapped):')
inject('def _extract_assertion_entities(assertion, soot_data_wrapped):')
inject('def _diagnose_variable_matching(var_names, var_scores, soot_data_wrapped, extraction_debug=None):')
inject('def _compute_semantic_outputs(assertion, soot_data_wrapped, total_score):')
inject('def score_assertion_from_statement(assertion: str, soot_data_wrapped):')

# Remove dynamic_data from function calls inside score_assertion_from_statement
text = re.sub(r'var_names, comparisons, extraction_debug = _extract_assertion_entities\(assertion, soot_data_wrapped,\s*dynamic_data\)', 'var_names, comparisons, extraction_debug = _extract_assertion_entities(assertion, soot_data_wrapped)', text)
text = re.sub(r'var_scores = quantify_assertion_value\(soot_data_wrapped,\s*dynamic_data\)', 'var_scores = quantify_assertion_value(soot_data_wrapped)', text)
text = re.sub(r'extraction_debug = _diagnose_variable_matching\(var_names, var_scores, soot_data_wrapped,\s*dynamic_data,\s*extraction_debug\)', 'extraction_debug = _diagnose_variable_matching(var_names, var_scores, soot_data_wrapped, extraction_debug)', text)
text = re.sub(r'semantic_payload = _compute_semantic_outputs\(assertion, soot_data_wrapped, dynamic_data, total_score\)', 'semantic_payload = _compute_semantic_outputs(assertion, soot_data_wrapped, total_score)', text)


with open('/home/ubuntu/myren/SF110/rewards_train_utils_no_dyn.py', 'w') as f:
    f.write(text)
