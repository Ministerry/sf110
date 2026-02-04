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
    assertion = data[i]['assert']
    if not is_method_worth_injecting(focal_method) or "catch" in data[i]['focal_prefix'] or "catch" in data[i]['focal_method'] or "void run" in data[i]['focal_prefix'] or '108' in data[i]['bug_num'] or '104' in data[i]['bug_num'] or '44' in data[i]['bug_num']:
        continue
    
    # 获取方法名和代码行数，用于启发式过滤
    extracted_method_name = data[i].get('extracted_method_name', '')
    # 计算有效代码行数（去除空行和简单的大括号）
    method_lines = [l for l in focal_method.split('\n') if l.strip() and l.strip() not in ['{', '}']]
    num_stmts = len(method_lines)

    # 3. 方法级启发式过滤 (Method Heuristics)
    # A. 过滤标准对象方法 (Standard Methods) - 这些通常是自动生成且琐碎的
    if extracted_method_name in ['equals', 'hashCode', 'toString', 'clone', 'compareTo']:
        continue
    
    # B. 过滤极短的方法 (Too Short) - 除非它包含了复杂的控制流
    # 对于 getter/setter (通常 < 3 行)，我们直接跳过，因为变异空间极小
    is_getter_setter = extracted_method_name.startswith(('get', 'set', 'is'))
    if num_stmts < 3:
        # 如果不是 getter/setter 但包含逻辑运算，可能值得保留；否则跳过
        if not any(c in focal_method for c in ['if', 'for', 'while', 'switch', 'return']):
            continue
        # 即使有逻辑，如果是简单的 getter/setter 也就跳过
        if is_getter_setter:
            continue

    # 1. 过滤琐碎断言 (Trivial Assertion Filter)
    # 提前过滤掉 assertTrue(true) 等无效数据，提高数据集质量
    trivial_pattern = r'assertTrue\s*\(\s*true\s*\)|assertFalse\s*\(\s*false\s*\)|assertNull\s*\(\s*null\s*\)'
    if re.search(trivial_pattern, assertion, re.IGNORECASE):
        continue

    # 2. 构建唯一键 (Unique Key Strategy)
    # 必须包含 assertion，否则会丢失同一前缀下的不同断言逻辑
    unique_key = (data[i]['focal_method'], data[i]['prefix'], assertion)

    test_class_path = test_method_path.replace('evosuite-tests','target/test-classes').replace('.java','.class')
    if os.path.exists(test_class_path) and unique_key not in visit:
        items.append(data[i])
        visit.append(unique_key)

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