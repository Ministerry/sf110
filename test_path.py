import json
import re
from inject import BugInject
import javalang
from javalang.tree import MethodDeclaration, TryStatement, CatchClause, MethodInvocation
from typing import List, Dict, Any,Optional,Set
import os
import random
import html
import pandas as pd
import shutil
import re
import subprocess
import javalang
import re
from filelock import FileLock
import time
import traceback
import tempfile
import argparse
import datetime
from pathlib import Path
from bs4 import BeautifulSoup
from inject import unparse
from utils import *

java_template = """
public class Test {{
    {code}
}}
"""
CWD = os.getcwd()
if __name__ == '__main__':
    with open("test.json","r",encoding="utf-8") as f:
        data = json.load(f)
    for i in range(len(data)):
        print(f"Processing item {i+1}/{len(data)}")
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
            shutil.copytree(src_project_path, project_path, dirs_exist_ok=False)
            relative_path = os.path.relpath(main_method_path, src_project_path)
            relatest_path = os.path.relpath(test_method_path, src_project_path)
            temp_main_method_path = os.path.join(project_path, relative_path)
            temp_test_method_path = os.path.join(project_path, relatest_path)
            lock_path = f"{temp_test_method_path}.lock"
            test_code = focal_prefix
            if assertion == "exception":
                test_code = f"@Test(timeout = 4000)\npublic void test{id}()  throws Throwable  " + "{\n" + "\ntry{\n" + prefix + "} catch (Throwable t) {\n\n}\n}"
            with FileLock(lock_path):
                replace_from_first_brace(temp_test_method_path, test_code, f"{bug_num}_{project}")
            test_ret = run_cmd_with_timeout(
                ['bash', 'compile.sh'],
                cwd=project_path
            )
            if test_ret is None or test_ret.returncode != 0:
                print("编译失败")
                continue
            test_class = test_name + 'EvoSuiteTest'
            test_ret = run_cmd_with_timeout(
                ['bash', 'run_test.sh', test_class],
                cwd=project_path
            )
            print(test_ret)
            if test_ret is None:
                print("运行失败")
                continue
            else:
                # /home/fdse/rmy/SF110/1_tullibee/report/jacoco-report/com.ib.client/AnyWrapperMsgGenerator.html
                test_class = '.'.join(test_name.split('.')[:-1]) + '/' + extracted_class_name + ".java.html"
                html_path = os.path.join(project_path,'report/jacoco-report',test_class)
                print(html_path)
                path = get_execution_paths(focal_method,html_path)
                java_code = java_template.format(code = focal_method)
                result = BugInject.buginject(java_code,extracted_method_name)
                final = []
                if result:
                    print("DEBUG: execution path:", path)
                    print(focal_method)
                    for j, cand in enumerate(result):
                        code = cand['code']
                        same_norm = (normalize_code(focal_method) == normalize_code(code))
                        path_match = is_path_based_variant_ast(focal_method, code, path)
                        print(f"[DEBUG] candidate #{j}: same_norm={same_norm}, path_match={path_match}")
                        # optional: show removed/added and candidate snippet for deeper debug
                        print(code)
                        if path_match and not same_norm:
                            final.append(cand)
                    data[i]['ast_generates'] = final
                    print(final)
        except Exception as e:
            print(f"[ERROR] 异步计算异常: {str(e)}")
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
    
    # sum = 0
    # for i in range(len(data)):
    #     sum += len(data[i]['ast_generates'])
    # print(sum)
    with open('test.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)