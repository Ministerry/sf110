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
with open('bug_inject_0.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)
result.extend(data[0:1000])  
with open('bug_inject_1000.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)
result.extend(data[1000:2000])  
with open('bug_inject_2000.json','r',encoding='utf-8') as f: # 1w -> 2w
    data = json.load(f)
result.extend(data[2000:3000])    
with open('bug_inject_3000.json','r',encoding='utf-8') as f: # 2w -> 3w
    data = json.load(f)
result.extend(data[3000:4000])  
with open('bug_inject_4000.json','r',encoding='utf-8') as f: # 2w -> 3w
    data = json.load(f)
result.extend(data[4000:5000])  
with open('bug_inject_5000.json','r',encoding='utf-8') as f: # 3w -> 4w
    data = json.load(f)
result.extend(data[5000:6000])   
with open('bug_inject_6000.json','r',encoding='utf-8') as f: # 6w -> 7w
    data = json.load(f)
result.extend(data[6000:7000])   
with open('bug_inject_7000.json','r',encoding='utf-8') as f: # 6w -> 7w
    data = json.load(f)
result.extend(data[7000:8000])   
with open('bug_inject_8000.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)
result.extend(data[8000:9000])  
with open('bug_inject_9000.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)
result.extend(data[9000:10000])  
with open('bug_inject_10000.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)
result.extend(data[10000:11000])  
with open('bug_inject_11000.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)
result.extend(data[11000:12000])  
with open('bug_inject_12000.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)
result.extend(data[12000:13000])  
with open('bug_inject_13000.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)
result.extend(data[13000:14000])    
with open('bug_inject_14000.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)
result.extend(data[14000:15000])  
with open('bug_inject_15000.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)
result.extend(data[15000:])  

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