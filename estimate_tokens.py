#!/usr/bin/env python3
"""Estimate token counts for dataset entries and report percentiles.

Usage:
  python estimate_tokens.py [path_to_json]

Tries to use tiktoken if installed; otherwise falls back to whitespace tokenization.
"""
import json
import sys
import os
import re
import math
import statistics


def try_import_tiktoken():
    try:
        import tiktoken
        return tiktoken
    except Exception:
        return None


def simple_tokenize(text: str):
    # fallback: count non-whitespace runs
    return len(re.findall(r"\S+", text))


def percentile(sorted_list, p):
    if not sorted_list:
        return 0
    k = (p / 100.0) * (len(sorted_list) - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_list[int(k)]
    d0 = sorted_list[int(f)] * (c - k)
    d1 = sorted_list[int(c)] * (k - f)
    return d0 + d1
instruction_text = """
# Role
You are an expert in Java unit testing and JUnit 4 test oracle generation.

# Task
Given the focal method and a test prefix, infer the INTENDED behavior and produce ONE precise, fully functional JUnit 4 test oracle that validates that behavior.

# Mandatory Rules
1. Assert ONLY on values returned or mutated by the focal method; ignore all setup variables.
2. NO SETUP CODE: do not create objects, assign variables, or call methods for preparation.
3. SINGLE OUTPUT: produce exactly one Java code block containing only the final oracle.
4. NO COMMENTS, NO EXPLANATIONS, NO PLACEHOLDERS, NO extra text.

# Exception Handling Rule
- If the test prefix ends with `// Undeclared exception!` output a try-catch-fail block in this exact pattern:
    try { focalMethodCall(); fail("Expecting exception: SpecificException"); } catch(SpecificException e) {}
    (replace SpecificException with the concrete exception class.)

# Formatting & Style
- Use JUnit 4 only and standard assertions (assertEquals, assertTrue, assertNotNull, assertSame, etc.).
- Do NOT repeat or echo any content from the test prefix.
- The output must be a single, compilable Java code block (one statement or one try-catch block).

# Output
Output ONLY the Java code block containing the oracle.
"""

def main(path):
    if not os.path.exists(path):
        print('File not found:', path)
        sys.exit(1)

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    tiktoken = try_import_tiktoken()
    if tiktoken:
        try:
            enc = tiktoken.get_encoding('gpt2')
        except Exception:
            try:
                enc = tiktoken.encoding_for_model('gpt2')
            except Exception:
                enc = None
    else:
        enc = None

    counts = []
    samples = []
    for i, item in enumerate(data):
        # combine instruction+input+output if present, else use concatenation of fields
        text = (instruction_text + item.get('prefix','') + '\n' + item.get('focal_method',''))
        # text = (item.get('instruction','') + '\n' + item.get('input','') + item.get('output',''))
        if enc is not None:
            try:
                n = len(enc.encode(text))
            except Exception:
                n = simple_tokenize(text)
        else:
            n = simple_tokenize(text)

        counts.append(n)
        if i < 20:
            samples.append((i, n, text[:200].replace('\n','\\n')))

    counts.sort()

    def show(p):
        return int(percentile(counts, p))

    p50 = show(50)
    p90 = show(90)
    p95 = show(95)
    p99 = show(99)
    p100 = counts[-1] if counts else 0

    mean = int(statistics.mean(counts)) if counts else 0

    print('Samples (first 20):')
    for i, n, txt in samples:
        print(f'  #{i} tokens={n} snippet="{txt}"')

    print('\nStats:')
    print(f'  count: {len(counts)}')
    print(f'  mean: {mean}')
    print(f'  p50: {p50}')
    print(f'  p90: {p90}')
    print(f'  p95: {p95}')
    print(f'  p99: {p99}')
    print(f'  max: {p100}')

    # suggest cutoff rounded up to nearest multiple of 64
    def roundup(x, base=64):
        return int(math.ceil(x / base) * base)

    suggested = roundup(p99, 64)
    print(f'\nSuggested cutoff_len (round up p99 to multiple of 64): {suggested}')


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'rl_train.json'
    main(path)
