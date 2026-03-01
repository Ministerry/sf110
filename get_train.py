import os
import random
import json

with open("bug_test.json","r",encoding="utf-8") as f:
    data = json.load(f)

random.shuffle(data)

with open('test.json', 'w', encoding='utf-8') as f:
        json.dump(data[:300], f, ensure_ascii=False, indent=2)