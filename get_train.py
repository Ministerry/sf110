import os
import random
import json

with open("bug_test_with_survived.json","r",encoding="utf-8") as f:
    data = json.load(f)

random.shuffle(data)

with open('qwen_train.json', 'w', encoding='utf-8') as f:
        json.dump(data[:600], f, ensure_ascii=False, indent=2)

with open('qwen_test.json', 'w', encoding='utf-8') as f:
        json.dump(data[600:750], f, ensure_ascii=False, indent=2)