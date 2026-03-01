import javalang
from javalang.tree import MethodDeclaration, TryStatement, CatchClause, MethodInvocation
import os
import json
from openai import OpenAI, APIConnectionError
import requests
import json
import pandas as pd
import time
import os
import re
import httpx 
from utils import *
result = []
for i in range(0,16):
    num = i * 1000
    with open(f'bug_inject_{num}.json','r',encoding='utf-8') as f: 
        data = json.load(f)
    for i in range(len(data)):
        result.append(data[i]) 

print(len(result))
sum = 0
for i in range(len(result)):
    # remove any generated AST entries where 'pass' == -1
    if 'ast_generates' in result[i] and isinstance(result[i]['ast_generates'], list):
        result[i]['ast_generates'] = [g for g in result[i]['ast_generates'] if (isinstance(g, dict) and g.get('pass') == 1)]

sum = 0
data = []
for i in range(len(result)):
    if len(result[i].get('ast_generates', [])) == 0:
        continue
    data.append(result[i])
    sum += len(result[i].get('ast_generates', []))
print(len(data))
print(sum)

with open('bug_test.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)