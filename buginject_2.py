import json
from inject import BugInject
from javalang.tree import MethodDeclaration, TryStatement, CatchClause, MethodInvocation
from typing import List, Dict, Any,Optional,Set
import os
import random
import pandas as pd
import shutil
from filelock import FileLock
import tempfile
from pathlib import Path
from inject import unparse
from bs4 import BeautifulSoup
from utils import *
import sys
java_template = """
public class Test {{
    {code}
}}
"""
CWD = os.getcwd()

if __name__ == '__main__':
    start_idx = int(sys.argv[1])
    end_idx = int(sys.argv[2])
    print(start_idx,end_idx)
    with open("assembled.json","r",encoding="utf-8") as f:
        data = json.load(f)
    for i in range(start_idx, end_idx):
        print(f"Processing item {i+1}/{len(data)}")
        if i % 100 == 0:
            with open(f'bug_inject_{start_idx}.json', 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        bug_varies = {
            "Null Reference Failures" : 0,
            "Incorrect Behavior Failures" : 0,
            "Index Boundary Failures" : 0,
            "Resource Management Failures" : 0,
            "Concurrent Modification Failures" : 0,
            "Logic Assertion Failures" : 0,
            "Data Integrity Failures" : 0,
            "Numeric Computation Failures" : 0,
            "String Processing Failures" : 0,
            "Return Failures" : 0,
        }
        temp_dir = None
        try:
            focal_method = data[i].get('focal_method', 0)
            focal_prefix = data[i].get('focal_prefix', 0)
            id = data[i].get('id', 0)
            prefix = data[i].get('prefix', '')
            project = data[i].get('project', '')
            bug_num = data[i].get('bug_num', '')
            assertion = data[i].get('assert', '')
            main_method_path = data[i].get('main_method_path', '')
            test_method_path = data[i].get('test_method_path', '')
            extracted_class_name = data[i].get('extracted_class_name', '')
            extracted_method_name = data[i].get('extracted_method_name', '')
            test_name = data[i].get('test_name', '')
            dir_name = f"{bug_num}_{project}"
            src_project_path = os.path.join(CWD, dir_name)
            temp_dir = tempfile.mkdtemp(prefix=f"reward_{bug_num}_{project}_")
            project_path = os.path.join(temp_dir, dir_name)
            # hardlink 
            subprocess.run(['cp', '-al', src_project_path, project_path], check=True, stderr=subprocess.DEVNULL)
        
            # 处理 target 目录，避免共享编译产物
            src_target = os.path.join(src_project_path, 'target')
            dst_target = os.path.join(project_path, 'target')
            if os.path.exists(dst_target):
                shutil.rmtree(dst_target) # 删除硬链接目录
                if os.path.exists(src_target):
                   shutil.copytree(src_target, dst_target) # 深度复制 target

            relative_path = os.path.relpath(main_method_path, src_project_path)
            relatest_path = os.path.relpath(test_method_path, src_project_path)
            temp_main_method_path = os.path.join(project_path, relative_path)
            temp_test_method_path = os.path.join(project_path, relatest_path)
        
            # 解除关键文件的硬链接，防止修改影响原项目与其他任务
            for fpath in [temp_main_method_path, temp_test_method_path]:
                if os.path.exists(fpath):
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content_tmp = f.read()
                    os.unlink(fpath)
                    with open(fpath, 'w', encoding='utf-8') as f:
                        f.write(content_tmp)

            # [新增] 构造隔离的测试环境
            # 必须只保留与当前任务相关的 prefix + insertion
            # 否则无法区分 bug 是违反了当前断言，还是违反了其他无关测试
            test_code = "public void test0()  throws Throwable {\n" + prefix + "\n" + assertion + "\n}"
            if assertion == "exception":
                continue 
            replace_from_first_brace(temp_test_method_path, test_code, f"{bug_num}_{project}")

            ast_generates = data[i].get('ast_generates', [])
            
            # 策略：数据平衡 (Data Balancing)
            # 针对每一种 bug_type，限制其保留的最大数量。
            # 这可以防止某一类容易生成的 Bug (如比较符替换) 淹没训练集，
            # 并大幅减少昂贵的编译检查次数。
            from collections import defaultdict
            LIMIT_PER_TYPE = 5 
            grouped_variants = defaultdict(list)
            for v in ast_generates:
                b_type = v.get('bug_type', 'General')
                grouped_variants[b_type].append(v)
            
            balanced_ast_generates = []
            for b_type, variants in grouped_variants.items():
                if len(variants) > LIMIT_PER_TYPE:
                    balanced_ast_generates.extend(random.sample(variants, LIMIT_PER_TYPE))
                else:
                    balanced_ast_generates.extend(variants)
            
            # 更新 data[i] 以便后续只保存保留下来的变体
            valid_variants = []
            
            for variant in balanced_ast_generates:
                code = variant['code']
                replace_result = replace_method_in_java_file(
                    temp_main_method_path,
                    extracted_class_name,
                    extracted_method_name,
                    code
                )
                backup_file = replace_result.get('backup_file')
                if not replace_result["success"]:
                    print(f"[ERROR] 替换方法失败: {replace_result['error']}")
                    if backup_file and temp_main_method_path and os.path.exists(temp_main_method_path):
                        restore_java_file_from_content(temp_main_method_path, backup_file)
                    continue         
                
                # 1. 编译检查
                test_ret = run_cmd_with_timeout(
                    ['bash', 'compile.sh'],
                    cwd=project_path
                )
                if test_ret is None or test_ret.returncode != 0:
                    print("变体编译失败")
                    variant['pass'] = -1 
                    if backup_file and temp_main_method_path and os.path.exists(temp_main_method_path):
                        restore_java_file_from_content(temp_main_method_path, backup_file)
                    continue
                
                # 2. 运行测试检查 (关键!)
                # 我们需要筛选出那些 *导致测试失败* 的变体 (returncode != 0)
                # 如果测试通过 (returncode == 0)，说明这是等价变体，对训练无效
                test_class = test_name + 'EvoSuiteTest'
                run_ret = run_cmd_with_timeout(
                    ['bash', 'run.sh', test_class],
                    cwd=project_path
                )
                
                if run_ret is not None and run_ret.returncode != 0:
                    # 编译通过 + 测试失败 = 完美的高质量 Bug
                    variant['pass'] = 1
                    valid_variants.append(variant)
                else:
                    print("变体通过了测试(等价变体)，跳过")
                    variant['pass'] = 0 

                if backup_file and temp_main_method_path and os.path.exists(temp_main_method_path):
                    restore_java_file_from_content(temp_main_method_path, backup_file)
            
            # 只保存验证过的有效变体
            data[i]['ast_generates'] = valid_variants
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            print(f"[ERROR] 异步计算异常: {str(e)}")
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    with open(f'bug_inject_{start_idx}.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)