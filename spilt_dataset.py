import os
import os
import random
import json
import re
import sys


def has_comment(item):
    """Heuristically determine whether an item contains comments.

    Checks several text-like fields for Java/line/block comment markers.
    """
    # Only regard a Javadoc-style block comment (/** ... */) that appears
    # before the method body (i.e. before the first '{') as a top comment.
    fields = ['raw_method']
    for k in fields:
        v = item.get(k)
        if not v:
            continue
        s = str(v)
        start_idx = s.find('/**')
        if start_idx == -1:
            continue
        end_idx = s.find('*/', start_idx + 3)
        if end_idx == -1:
            continue
        first_brace = s.find('{')
        # If there's no brace, treat the comment as top-level; otherwise
        # ensure the Javadoc start is located before the first '{'.
        if first_brace == -1 or start_idx < first_brace:
            return True
    return False


def main(input_path="items_test.json"):
    if not os.path.exists(input_path):
        print(f"Input file not found: {input_path}")
        return

    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    random.shuffle(data)

    havedoc = []
    nodoc = []
    for item in data:
        if has_comment(item):
            havedoc.append(item)
        else:
            nodoc.append(item)

    with open('rl_nodoc.json', 'w', encoding='utf-8') as f:
        json.dump(nodoc, f, ensure_ascii=False, indent=2)

    with open('rl_havedoc.json', 'w', encoding='utf-8') as f:
        json.dump(havedoc, f, ensure_ascii=False, indent=2)

    print(f"Total items: {len(data)}")
    print(f"With comments: {len(havedoc)} -> havedoc.json")
    print(f"Without comments: {len(nodoc)} -> nodoc.json")


if __name__ == '__main__':
    inp = sys.argv[1] if len(sys.argv) > 1 else 'bug_test.json'
    main(inp)