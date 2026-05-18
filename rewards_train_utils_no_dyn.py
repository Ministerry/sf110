import re
import json
import javalang
from openai import OpenAI, APIConnectionError
from config import DEEPSEEK_API_KEY

SEMANTIC_REWARD_ENABLED = False

weights = {
    "role_return": 30.0,
    "role_focal": 15.0,
    "role_input": 5.0,
    "role_constructor": 5.0,
    "role_other": 2.0,
    
    "branch_influence": 2.0,
    "exception_paths": 1.0,
    "pdg_dependants": 0.5,
    "fields_read": 1.0,
    "fields_modified": 2.0,
    "static_fields_read": 0.5,
    "static_fields_modified": 1.0,
    
    "fusion_w_heuristic": 0.75,
    "fusion_w_semantic": 0.25,
}

def extract_assertion_label(assertion: str) -> str:
    low = assertion.lower()
    if 'assertequals' in low: return "assertEquals"
    if 'asserttrue' in low: return "assertTrue"
    if 'assertfalse' in low: return "assertFalse"
    if 'assertnull' in low: return "assertNull"
    if 'assertnotnull' in low: return "assertNotNull"
    if 'assertthrows' in low: return "assertThrows"
    if 'assertthat' in low: return "assertThat"
    return "unknown"

def _extract_assertion_entities(assertion, soot_data_wrapped):
    variables = []
    if isinstance(soot_data_wrapped, dict) and "variables" in soot_data_wrapped:
        variables = soot_data_wrapped["variables"]
    elif isinstance(soot_data_wrapped, list):
        variables = soot_data_wrapped
        
    known_vars = {v.get("variable") for v in variables if isinstance(v, dict)}
    
    found_vars = set()
    try:
        tokens = list(javalang.tokenizer.tokenize(assertion))
        for token in tokens:
            if isinstance(token, javalang.tokenizer.Identifier) and token.value in known_vars:
                found_vars.add(token.value)
    except:
        for var in known_vars:
            if var and re.search(r'\b' + re.escape(var) + r'\b', assertion):
                found_vars.add(var)
    return list(found_vars)

def quantify_assertion_value(soot_data_wrapped, found_vars):
    variables = []
    if isinstance(soot_data_wrapped, dict) and "variables" in soot_data_wrapped:
        variables = soot_data_wrapped["variables"]
    elif isinstance(soot_data_wrapped, list):
        variables = soot_data_wrapped

    total_score = 0.0
    breakdown = {}

    for var in variables:
        v_name = var.get("variable")
        if v_name not in found_vars:
            continue
            
        role = var.get("role", "")
        cfi = var.get("control_flow_influence", {})
        dfa = var.get("data_flow_state_access", {})
        flow = var.get("flow_influence", {})
        df_usage = var.get("data_flow_usage", {})
        
        var_score = 0.0
        
        # 1. Role Score
        if "Return Value" in role:
            var_score += weights["role_return"]
        elif "Focal Object" in role:
            var_score += weights["role_focal"]
        elif "Input Argument" in role:
            var_score += weights["role_input"]
        elif "Constructor Argument" in role:
            var_score += weights["role_constructor"]
        else:
            var_score += weights["role_other"]
            
        # 2. Control Flow Influence
        var_score += cfi.get("branch_decisions_dependent_on_state", 0) * weights["branch_influence"]
        var_score += cfi.get("branch_decisions", 0) * weights["branch_influence"]
        var_score += cfi.get("exception_paths", 0) * weights["exception_paths"]
        var_score += cfi.get("pdg_dependants", 0) * weights["pdg_dependants"]
        
        # 3. Data Flow Access
        var_score += dfa.get("fields_read", 0) * weights["fields_read"]
        var_score += dfa.get("fields_modified", 0) * weights["fields_modified"]
        var_score += dfa.get("static_fields_read", 0) * weights["static_fields_read"]
        var_score += dfa.get("static_fields_modified", 0) * weights["static_fields_modified"]
        
        # Flow usage for params/constructors
        var_score += flow.get("backing_data_read", 0) * weights["fields_read"]
        var_score += flow.get("backing_data_modified", 0) * weights["fields_modified"]
        var_score += df_usage.get("reads", 0) * weights["fields_read"]
        var_score += df_usage.get("writes", 0) * weights["fields_modified"]

        total_score += var_score
        breakdown[v_name] = var_score

    return total_score, breakdown

def create_client():
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY environment variable is not set.")
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")

def model_judge(prompt: str, model="deepseek-chat", temperature=0.0, timeout=60.0):
    try:
        client = create_client()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are an expert Java developer and tester."},
                {"role": "user", "content": prompt}
            ],
            temperature=temperature,
            timeout=timeout
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[LLM error] {e}")
        return "{ \"score\": 0.5, \"confidence\": 0.5, \"reasoning\": \"Error\" }"

def assess_assertion_via_ir(assertion: str, sample: dict, model="deepseek-chat"):
    prompt = f"""Evaluate the semantic value of this assertion:
Assertion: {assertion}
Context: This assertion tests the behavior of `{sample.get('extracted_method_name', 'unknown')}`.

Output ONLY a JSON object with:
- score (0.0 to 1.0): How effective and meaningful is this assertion?
- confidence (0.0 to 1.0): Your confidence.
- reasoning (string): Brief explanation.
"""
    result_str = model_judge(prompt, model=model)
    try:
        import re
        json_match = re.search(r'\{.*\}', result_str, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            return data.get('score', 0.5), data.get('confidence', 0.5), data
    except:
        pass
    return 0.5, 0.5, {"raw": result_str}

def score_assertion_from_statement(assertion: str, soot_data_wrapped):
    found_vars = _extract_assertion_entities(assertion, soot_data_wrapped)
    heuristic_score, breakdown = quantify_assertion_value(soot_data_wrapped, found_vars)
    
    # Cap and normalize heuristic score
    cap = 50.0
    normalized_heuristic = min(heuristic_score, cap) / cap
    
    label = extract_assertion_label(assertion)
    
    semantic_score = 0.5
    llm_payload = {}
    if SEMANTIC_REWARD_ENABLED:
        sem_score, sem_conf, llm_payload = assess_assertion_via_ir(assertion, {"extracted_method_name": "method"})
        semantic_score = sem_score
        
        final_reward = (normalized_heuristic * weights["fusion_w_heuristic"] + 
                        semantic_score * weights["fusion_w_semantic"])
    else:
        final_reward = normalized_heuristic

    return {
        "final_reward": final_reward,
        "heuristic_score": heuristic_score,
        "normalized_heuristic": normalized_heuristic,
        "semantic_score": semantic_score,
        "assertion_label": label,
        "variables_found": found_vars,
        "breakdown": breakdown,
        "llm_payload": llm_payload
    }
