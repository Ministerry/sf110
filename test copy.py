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
def strip_java_guard(s: str) -> str:
    """
    如果字符串中包含类似 ```java\nassertTrue(...);\n``` 或 java\nassertTrue(...);\n 的格式，
    返回只包含 assertTrue(...) 那一行。否则返回原字符串。
    """
    s = s or ""
    # 去掉 markdown/code fence（```java ... ```）
    s = re.sub(r'```(?:\w+)?\s*', '', s)
    s = re.sub(r'\s*```', '', s)
    # 查找带分号的断言（优先）
    m = re.search(r'(assertTrue|assert)\s*\([^;]*\);\s*', s, re.DOTALL)
    if m:
        return m.group(0).strip()
    # 兼容没有分号的情况
    m2 = re.search(r'(assertTrue|assert)\s*\([^)]*\)\s*', s, re.DOTALL)
    if m2:
        return m2.group(0).strip()
    return s



# with open(generation_path,"r",encoding="utf-8") as f:
#     for line in f:
#         pred = json.loads(line.strip())
#         generation.append(strip_java_guard(pred['predict']))
# gen_count = 0
# for i in range(len(result)):
#     if len(result[i]['ast_generates']) != 0:
#         result[i]['predict'] = generation[gen_count]
#         gen_count += 1
#         data.append(result[i])
# with open("assembled.json","w",encoding="utf-8") as f:
#     json.dump(data, f, ensure_ascii=False, indent=2)


def extract_catch_exception_types(java_src: str):
    """
    从 Java 源代码字符串中提取所有 catch 块所捕获的异常类型，返回按出现顺序去重的列表。

    例子:
      catch(IllegalArgumentException e) -> ['IllegalArgumentException']
      catch(final java.io.IOException | SQLException ex) -> ['IOException', 'SQLException']
    """
    import re
    types_found = []
    for m in re.finditer(r'catch\s*\((.*?)\)', java_src, re.S):
        inside = m.group(1).strip()
        # 删除注解
        inside = re.sub(r'@\w+(?:\([^)]*\))?\s*', '', inside)
        # 拆分为 token，最后一个 token 通常是变量名
        tokens = inside.split()
        if not tokens:
            continue
        if len(tokens) == 1:
            types_part = tokens[0]
        else:
            types_part = ' '.join(tokens[:-1])
        # 移除修饰符
        types_part = re.sub(r'\b(final|volatile|transient)\b\s*', '', types_part)
        # 按 | 分割多异常
        raw_types = [t.strip() for t in re.split(r'\|', types_part) if t.strip()]
        for t in raw_types:
            # 去掉泛型和数组标记，取最后一个点分段作为短类名
            t = re.sub(r'<.*?>', '', t).replace('[]', '').strip()
            short = t.split('.')[-1].strip()
            if short and short not in types_found:
                types_found.append(short)
    return types_found


def extract_first_catch_exception(java_src: str):
    """
    返回源码中第一个 catch 块捕获的异常类型字符串，找不到时返回 None。
    """
    types = extract_catch_exception_types(java_src)
    return types[0] if types else None

java = '''
  try {
    // ...
  } catch(IllegalArgumentException e) {
    // ...
  } catch(final java.io.IOException | SQLException ex) {
    // ...
  }
'''

with open('assembled.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)
for i in range(len(data)):
    data[i]['main_method_path'] = data[i]['main_method_path'].replace('fdse/rmy','ubuntu/myren')
    data[i]['test_method_path'] = data[i]['test_method_path'].replace('fdse/rmy','ubuntu/myren')

visit = []
result = []
for i in range(len(data)):
    if (data[i]['focal_method'],data[i]['prefix']) not in visit:
        visit.append((data[i]['focal_method'],data[i]['prefix']))
        result.append(data[i])

print(len(result))
with open('test.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

random.shuffle(result)
with open('bug_test.json', 'w', encoding='utf-8') as f:
    json.dump(result[:300], f, ensure_ascii=False, indent=2)