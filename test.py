import javalang
from javalang.tree import MethodDeclaration, TryStatement, CatchClause, MethodInvocation
import os
import json
import random
from openai import OpenAI, APIConnectionError
import requests
import json
import pandas as pd
import time
import os
import re
import httpx 
from utils import *

with open('items_test.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)

items = []
visit = []
for i in range(len(data)):
    test_method_path = data[i]['test_method_path']
    focal_method = data[i]['focal_method']
    if not is_method_worth_injecting(focal_method) or "catch" in data[i]['focal_prefix'] or "catch" in data[i]['focal_method'] or "void run" in data[i]['focal_prefix'] or '108' in data[i]['bug_num'] or '104' in data[i]['bug_num'] or '44' in data[i]['bug_num']:
        continue
    num = data[i]['bug_num']
    test_class_path = test_method_path.replace('evosuite-tests','target/test-classes').replace('.java','.class')
    if os.path.exists(test_class_path) and (data[i]['focal_method'],data[i]['prefix']) not in visit:
        items.append(data[i])
        visit.append((data[i]['focal_method'],data[i]['prefix']))

with open('buginject_test.json', 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

print(len(items))

# with open('bug_test.json','r',encoding='utf-8') as f: # 0 -> 1w
#     data = json.load(f)
# for i in range(len(data)):
#     data[i]['main_method_path'] = data[i]['main_method_path'].replace('fdse/rmy/SF110','ubuntu/myren/SF110')
#     data[i]['test_method_path'] = data[i]['test_method_path'].replace('fdse/rmy/SF110','ubuntu/myren/SF110')

# visit = []
# items = []
# for i in range(len(data)):
#     test_method_path = data[i]['test_method_path']
#     focal_method = data[i]['focal_method']
#     if not is_method_worth_injecting(focal_method) or "try {" in data[i]['focal_prefix'] or "void run" in data[i]['focal_prefix'] or '108' in data[i]['bug_num']:
#         continue
#     num = data[i]['bug_num']
#     test_class_path = test_method_path.replace('evosuite-tests','target/test-classes').replace('.java','.class')
#     if os.path.exists(test_class_path) and (data[i]['focal_method'],data[i]['prefix']) not in visit:
#         items.append(data[i])
#         visit.append((data[i]['focal_method'],data[i]['prefix']))

# print(len(items))
# with open('test.json', 'w', encoding='utf-8') as f:
#     json.dump(items, f, ensure_ascii=False, indent=2)
# result = []
# print(len(data))
# sum_1 = 0
# sum_2 = 0
# sum_3 = 0
# for i in range(len(data)):
#     if data[i]['bug_num'] == "108" or data[i]['bug_num'] == "104" or data[i]['bug_num'] == "44":
#         continue
#     result.append(data[i])
#     sum_1 += 1
#     sum_2 += len(data[i]['ast_generates'])
#     if "void" not in data[i]['focal_method'] :
#         sum_3 += 1
# print(sum_1)
# print(sum_2)
# print(sum_3)
# print(len(result))
# random.shuffle(result)
# with open('qwen_train.json', 'w', encoding='utf-8') as f:
#     json.dump(result[:300], f, ensure_ascii=False, indent=2)