#!/usr/bin/env python3
"""spec_scorer_demo.py
Refactored demo for scoring assertion relevance against method spec extracted from comments.

Features added:
- Modular helper functions: extract_comments, parse_spec, extract_predicates, heuristic_score
- CLI options to run examples and save results to JSON/CSV
- More robust token and comparison matching
- Optional external model scorer via callable
"""

import json
import re
import argparse
import csv
from difflib import SequenceMatcher

# try to import live model judge from api.py; api.py is guarded so import is safe
try:
    from api import model_judge
except Exception:
    model_judge = None


def extract_comments(raw_method: str) -> str:
    m = re.search(r"/\*\*(.*?)\*/", raw_method, re.S)
    if m:
        return m.group(1).strip()
    # fallback: collect leading line comments before signature
    lines = raw_method.splitlines()
    collected = []
    for ln in lines:
        if re.match(r"\s*(public|private|protected|static|final|\w).*\(.*\)\s*\{", ln):
            break
        if ln.strip().startswith('//') or ln.strip().startswith('/*') or ln.strip().startswith('*'):
            collected.append(re.sub(r'^\s*/*\*+\s?', '', ln).strip())
    return '\n'.join(collected).strip()


def parse_spec(comments_text: str) -> dict:
    spec = {'summary': '', 'params': {}, 'return': '', 'throws': [], 'raw_comments': comments_text}
    if not comments_text:
        return spec
    sents = re.split(r'[\.\n]\s*', comments_text)
    if sents:
        spec['summary'] = sents[0].strip()
    for tag in re.finditer(r'@param\s+(\w+)\s+(.*)', comments_text):
        spec['params'][tag.group(1)] = tag.group(2).strip()
    mret = re.search(r'@return\s+(.*)', comments_text)
    if mret:
        spec['return'] = mret.group(1).strip()
    for thr in re.finditer(r'@throws\s+(\w+)\s+(.*)', comments_text):
        spec['throws'].append({'exc': thr.group(1), 'desc': thr.group(2).strip()})
    preconds = []
    postconds = []
    for line in re.split(r'\n+', comments_text):
        low = line.lower()
        if any(k in low for k in ['if ', 'when ', 'must ', 'should ', 'require', 'precondition']):
            preconds.append(line.strip())
        elif any(k in low for k in ['return', 'returns', 'postcondition', 'ensure', 'then ']):
            postconds.append(line.strip())
    spec['preconditions'] = preconds
    spec['postconditions'] = postconds
    return spec


def extract_predicates(spec: dict) -> list:
    predicates = []
    texts = [spec.get('summary', '')] + list(spec.get('params', {}).values()) + [spec.get('return', '')] + spec.get('preconditions', []) + spec.get('postconditions', [])
    for txt in texts:
        if not txt:
            continue
        for m in re.finditer(r'([\w\.]+)\s*(>=|<=|==|!=|>|<)\s*([0-9]+|null|true|false)', txt, re.I):
            predicates.append({'expr': m.group(0), 'left': m.group(1), 'op': m.group(2), 'right': m.group(3)})
        if re.search(r'\b(size|length|empty)\b', txt, re.I):
            predicates.append({'expr': txt.strip(), 'kind': 'size_hint'})
        if re.search(r'\bnull\b', txt, re.I):
            predicates.append({'expr': txt.strip(), 'kind': 'null_hint'})
    return predicates


def heuristic_score(assertion: str, comments_text: str, predicates: list) -> (float, dict):
    """Compute heuristic score in [0,1] and return components."""
    def word_tokens(s):
        return re.findall(r"[a-zA-Z0-9_]+", s.lower())

    a_tokens = set(word_tokens(assertion))
    spec_tokens = set(word_tokens(comments_text))
    overlap = len(a_tokens & spec_tokens)
    union = max(1, len(a_tokens | spec_tokens))
    token_score = overlap / union

    a_low = assertion.lower()
    op_bonus = 0.0
    matched = []
    for p in predicates:
        if 'op' in p:
            left = p['left'].lower()
            right = (p.get('right') or '').lower()
            if left in a_low:
                if right and right in a_low or p['op'] in a_low:
                    op_bonus += 0.25
                    matched.append(p['expr'])
                else:
                    op_bonus += 0.1
        elif p.get('kind') == 'size_hint' and re.search(r'\b(size|length|isempty)\b', a_low):
            op_bonus += 0.35
            matched.append(p.get('expr'))
        elif p.get('kind') == 'null_hint' and 'null' in a_low:
            op_bonus += 0.2
            matched.append(p.get('expr'))

    comp_matches = 0
    for m in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_\.]*)\s*(>=|<=|>|<|==|!=)\s*([0-9]+|null|true|false)', assertion):
        left, op, right = m.group(1).lower(), m.group(2), m.group(3).lower()
        for p in predicates:
            if p.get('left') and (p.get('left').lower() == left or p.get('left').lower() in left):
                if right in (p.get('right') or '').lower() or op in (p.get('op') or ''):
                    comp_matches += 1
                    matched.append(p.get('expr'))
    comp_score = min(1.0, comp_matches * 0.4)

    heuristic_raw = 0.55 * token_score + 0.3 * min(1.0, op_bonus) + 0.15 * comp_score
    heuristic_score_val = max(0.0, min(1.0, heuristic_raw))
    return heuristic_score_val, {'token_score': token_score, 'op_bonus': op_bonus, 'comp_score': comp_score, 'matched': list(dict.fromkeys(matched))}


def score_assertion_against_spec(assertion: str, raw_method: str, model_scorer=None, weights=None):
    cfg = {'heuristic_weight': 1.0, 'model_weight': 0.0}
    if isinstance(weights, dict):
        cfg.update(weights)
    comments_text = extract_comments(raw_method)
    spec = parse_spec(comments_text)
    predicates = extract_predicates(spec)
    intermediate_code = '\n'.join([p.get('expr') or p.get('kind') or str(p) for p in predicates]) or comments_text
    h_score, comps = heuristic_score(assertion, comments_text, predicates)
    model_score = None
    if callable(model_scorer):
        # build JSON-prompt for strict JSON output from model
        prompt = {
            'spec': comments_text,
            'intermediate': intermediate_code,
            'assertion': assertion,
            'instructions': (
                'Please return a single JSON object with fields: score (0..1), confidence (0..1), ' \
                'matched_predicates (list), category (strong|partial|weak|irrelevant), rationale (short), ' \
                'recommended_action (short).' )
        }
        try:
            res = model_scorer(json.dumps(prompt))
            if isinstance(res, (int, float)):
                model_score = float(res)
            else:
                # If model returns parsed dict-like, try to extract score
                try:
                    if isinstance(res, dict) and 'score' in res:
                        model_score = float(res['score'])
                except Exception:
                    model_score = None
        except Exception:
            model_score = None

    if model_score is not None and cfg['model_weight'] > 0:
        final = cfg['model_weight'] * model_score + cfg['heuristic_weight'] * h_score
        denom = cfg['model_weight'] + cfg['heuristic_weight']
        final_score = final / max(1e-9, denom)
    else:
        final_score = h_score

    return {
        'spec': spec,
        'intermediate_code': intermediate_code,
        'heuristic_score': h_score,
        'heuristic_components': comps,
        'model_score': model_score,
        'final_score': final_score
    }


def dummy_model_scorer(prompt: str) -> float:
    """Simple stub model scorer: returns higher score when prompt mentions size/count or relational ops."""
    p = prompt.lower() if isinstance(prompt, str) else json.dumps(prompt).lower()
    if any(k in p for k in ("size", "length", "count", "> 0", ">=", "<=", "null")):
        return 0.9
    return 0.3


EXAMPLES = [
    {
        'name': 'matches_size_hint',
        'raw_method': '''
        /**
         * Returns the number of active items.
         * @return the count of active items; may be 0 when none
         */
        public int getActiveCount() { /* ... */ }
        ''',
        'assertion': 'assertTrue(getActiveCount() > 0);'
    },
    {
        'name': 'unrelated_assertion',
        'raw_method': '''
        /**
         * Fetches the user name or null if not present.
         * @return user name or null
         */
        public String getUserName() { /* ... */ }
        ''',
        'assertion': 'assertNotNull(someOtherObj.getId());'
    }
]


def run_examples():
    results = []
    for ex in EXAMPLES:
        res = score_assertion_against_spec(
            ex['assertion'],
            ex['raw_method'],
            model_scorer=dummy_model_scorer,
            weights={'heuristic_weight': 1.0, 'model_weight': 1.0}
        )
        results.append({'name': ex['name'], 'assertion': ex['assertion'], 'final_score': res['final_score'], 'heuristic_score': res['heuristic_score'], 'model_score': res['model_score'], 'heuristic_components': res.get('heuristic_components'), 'intermediate': res['intermediate_code']})
    print(json.dumps(results, indent=2))


def save_results_csv(results, path):
    keys = ['name', 'assertion', 'final_score', 'heuristic_score', 'model_score', 'heuristic_components', 'intermediate']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for r in results:
            row = {k: json.dumps(r.get(k)) if isinstance(r.get(k), (dict, list)) else r.get(k) for k in keys}
            writer.writerow(row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--save-csv', help='Save example results to CSV path', default=None)
    parser.add_argument('--use-api', help='Use live model judge from api.py (requires API key)', action='store_true')
    args = parser.parse_args()
    results = []
    for ex in EXAMPLES:
        # choose model_scorer: live if requested and available, otherwise dummy
        ms = dummy_model_scorer
        if args.use_api and model_judge is not None:
            ms = model_judge
        res = score_assertion_against_spec(
            ex['assertion'], ex['raw_method'], model_scorer=ms, weights={'heuristic_weight': 1.0, 'model_weight': 1.0}
        )
        results.append({'name': ex['name'], 'assertion': ex['assertion'], 'final_score': res['final_score'], 'heuristic_score': res['heuristic_score'], 'model_score': res['model_score'], 'heuristic_components': res.get('heuristic_components'), 'intermediate': res['intermediate_code']})
    print(json.dumps(results, indent=2, ensure_ascii=False))
    if args.save_csv:
        save_results_csv(results, args.save_csv)


if __name__ == '__main__':
    main()
