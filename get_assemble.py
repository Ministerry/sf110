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
#23314
with open('bug_inject_test_1.json','r',encoding='utf-8') as f: 
    data = json.load(f)
for i in range(len(data)):
    result.append(data[i])  
    
with open('bug_inject_test_2.json','r',encoding='utf-8') as f: 
    data = json.load(f)
for i in range(len(data)):
    result.append(data[i])    

with open('bug_inject_test_3.json','r',encoding='utf-8') as f: 
    data = json.load(f)
for i in range(len(data)):
    result.append(data[i])    
    
with open('bug_inject_test_4.json','r',encoding='utf-8') as f: 
    data = json.load(f)
for i in range(len(data)):
    result.append(data[i])    
with open('bug_inject_test_5.json','r',encoding='utf-8') as f: 
    data = json.load(f)
for i in range(len(data)):
    result.append(data[i])   
with open('bug_inject_test_6.json','r',encoding='utf-8') as f: 
    data = json.load(f)
for i in range(len(data)):
    result.append(data[i])   
with open('bug_inject_test_7.json','r',encoding='utf-8') as f: 
    data = json.load(f)
for i in range(len(data)):
    result.append(data[i]) 
print(len(result))
sum = 0
for i in range(len(result)):
    sum += len(result[i]['ast_generates'])
print(sum)

with open('assembled.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)