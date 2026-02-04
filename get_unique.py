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


data_path = "/home/ubuntu/myren/SF110/single_return_methods.json"
with open(data_path,'r',encoding='utf-8') as f:
    data = json.load(f)
items = []
visit = []
id = 0
for i in range(len(data)):
    if len(data[i]['test_partten']) != 0:
        for j in range(len(data[i]['test_partten'])) :
            if len(data[i]['test_partten'][j]['result']) != 0:
                for k in range(len(data[i]['test_partten'][j]['result'])):
                    # 检查 1: 长度过滤
                    items.append({
                        "id" : id,
                        "focal_method" : data[i]['test_partten'][j]['result'][k]['focal_method'],
                        "raw_method" : data[i]['test_partten'][j]['result'][k]['raw_method'],
                        "project" : data[i]['project'],
                        "bug_num" : data[i]['bug_num'],
                        "test_name" : data[i]['test_partten'][j]['test_name'],
                        "prefixes" : data[i]['test_partten'][j]['result'][k]['prefixes'],
                        "main_method_path" : data[i]['test_partten'][j]['main_path'],
                        "test_method_path" : data[i]['test_partten'][j]['method_path'],
                        "extracted_class_name" : data[i]['test_partten'][j]['result'][k]['class'],
                        "extracted_method_name" : data[i]['test_partten'][j]['result'][k]['method'],
                    })
                    id += 1


id = 0
data = []
seen_pairs = set() # 用作去重集合

for i in range(len(items)):
    print(f"Processing item {i+1}/{len(items)}")
    for j in range(len(items[i]['prefixes'])):
        single = extract_prefix_and_asserts(items[i]['focal_method'],items[i]['prefixes'][j])
        for k in range(len(single)):
            # 去除可能的首尾空白
            assertion = single[k].get('assert', '').strip()
            prefix = single[k].get('prefix', '').strip()
            
            # 1. 基础非空检查
            if not assertion:
                continue
                
            # 2. 构造去重键
            # 使用 (focal_method, prefix, assert) 三元组来确保唯一性
            # Focal Method 决定了被测对象
            # Prefix 决定了测试状态
            # Assert 决定了验证逻辑
            # 如果这三者都一样，那就是完全重复的数据
            unique_key = (items[i]['focal_method'], prefix, assertion)
            
            if unique_key in seen_pairs:
                continue
            
            seen_pairs.add(unique_key)
            
            item = {
                "id" : id,
                "focal_method" : items[i]['focal_method'],
                "raw_method" : items[i]['raw_method'],
                "project" : items[i]['project'],
                "bug_num" : items[i]['bug_num'],
                "test_name" : items[i]['test_name'],
                "focal_prefix": items[i]['prefixes'][j],
                "prefix" : prefix,
                "assert" : assertion,
                "main_method_path" : items[i]['main_method_path'],
                "test_method_path" : items[i]['test_method_path'],
                "extracted_method_name" : items[i]['extracted_method_name'],
                "extracted_class_name" : items[i]['extracted_class_name'],
            }
            id += 1
            data.append(item)


print(len(data))
with open('items_test.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# 找到对应的prefix