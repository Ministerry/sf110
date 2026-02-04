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
java_template = """
public class Test {{
    {code}
}}
"""
CWD = os.getcwd()

if __name__ == '__main__':
    with open("buginject_test.json","r",encoding="utf-8") as f:
        data = json.load(f)
    # random.shuffle(data)
    items = []
    # sum = 0
    for i in range(len(data)):
        test_method_path = data[i]['test_method_path']
        focal_method = data[i]['focal_method']
        if not is_method_worth_injecting(focal_method):
            continue
        num = data[i]['bug_num']
        test_class_path = test_method_path.replace('evosuite-tests','target/test-classes').replace('.java','.class')
        if os.path.exists(test_class_path):
            items.append(data[i])
        #     sum += 1
        # if sum == 100:
        #     break
    print(len(items))
    data = items
    result = []
    for i in range(18000,21751):
        print(f"Processing item {i+1}/{len(data)}")
        id = data[i].get('id', 0)
        if i % 100 == 0:
            with open('bug_inject_test_7.json', 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        # bug_varies = {
        #     "Null Reference Failures" : 0,
        #     "Incorrect Behavior Failures" : 0,
        #     "Index Boundary Failures" : 0,
        #     "Resource Management Failures" : 0,
        #     "Concurrent Modification Failures" : 0,
        #     "Logic Assertion Failures" : 0,
        #     "Data Integrity Failures" : 0,
        #     "Numeric Computation Failures" : 0,
        #     "String Processing Failures" : 0,
        # }
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

            # [关键路径决策] 
            # 使用 prefix + assertion 来构造最小化的测试执行环境
            # 而不是使用 focal_prefix (其中可能包含多个无关断言)
            # 这确保了捕获的 path 是精准对应于当前“待学习”的 assertion 的
            test_code = "public void test0()  throws Throwable {\n" + prefix + "\n" + assertion + "\n}"
            if assertion == "exception":
                continue
            replace_from_first_brace(temp_test_method_path, test_code, f"{bug_num}_{project}")
            test_ret = run_cmd_with_timeout(
                ['bash', 'compile.sh'],
                cwd=project_path
            )
            if test_ret is None or test_ret.returncode != 0:
                print("编译失败")
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                continue
            test_class = test_name + 'EvoSuiteTest'
            test_ret = run_cmd_with_timeout(
                ['bash', 'run_test.sh', test_class],
                cwd=project_path
            )
            if test_ret is None:
                print("运行失败")
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                continue
            else:
                # /home/fdse/rmy/SF110/1_tullibee/report/jacoco-report/com.ib.client/AnyWrapperMsgGenerator.html
                test_class = '.'.join(test_name.split('.')[:-1]) + '/' + extracted_class_name + ".java.html"
                html_path = os.path.join(project_path,'report/jacoco-report',test_class)
                print(html_path)
                path = get_execution_paths(focal_method,html_path)
                print(path)
                data[i]['path'] = path
                # single = extract_prefix_and_asserts(data[i]['focal_prefix'],assertion)
                # if "catch" in focal_prefix or "run()" in focal_prefix:
                #     data[i]['prefix'] = single[0]['prefix']
                java_code = java_template.format(code = focal_method)
                injects = BugInject.buginject(java_code,extracted_method_name)
                final = []
                if injects:
                    for j, cand in enumerate(injects):
                        code = cand['code']
                        bug_type = cand['bug_type']
                        # if bug_varies[bug_type] == 3:
                        #     continue
                        path_match = is_path_based_variant_ast(focal_method, code, path)
                        if path_match:
                            final.append(cand)
                            # bug_varies[bug_type] += 1
                    data[i]['ast_generates'] = final
                    if len(final) != 0:
                        result.append(data[i])
                        # sum += 1
                        # if sum == 300:
                        #     break
        except Exception as e:
            print(f"[ERROR] 异步计算异常: {str(e)}")
        finally:
            # if bug_num == '108':
            #     continue
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    with open('bug_inject_test_7.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)




# if __name__ == '__main__':
#     with open("buginject_test.json","r",encoding="utf-8") as f:
#         data = json.load(f)
#     for i in range(500):
#         print(i)
#         focal_method = data[i]['focal_method']
#         main_method_path = data[i]['main_method_path']
#         extracted_method_name = data[i]['extracted_method_name']
#         # with open(main_method_path,"r",encoding="utf-8") as f:
#         #     java_code = f.read()
#         # 解析代码并找到目标方法
#         java_code = java_template.format(code = focal_method)
#         result = BugInject.buginject(java_code,extracted_method_name)
#         if result != None:
#             data[i]['ast_generates'] = result
#         else :
#             data[i]['ast_generates'] = []
#     # generates = []
#     # visit = []
#     # for i in range(len(data)):
#     #     for j in range(len(data[i]['ds_generates'])):
#     #         if normalize_code(data[i]['ds_generates'][j]['code']) not in visit:
#     #             generates.append(data[i]['ds_generates'][j])
#     #             visit.append(normalize_code(data[i]['ds_generates'][j]['code']))
#     #     for j in range(len(data[i]['ast_generate'])):
#     #         if normalize_code(data[i]['ast_generate'][j]['code']) not in visit:
#     #             generates.append(data[i]['ast_generate'][j])
#     #             visit.append(normalize_code(data[i]['ast_generate'][j]['code']))
#     #     del data[i]['ds_generates']
#     #     del data[i]['ast_generate']
#     #     data[i]['generates'] = generates
#     #     generates = []       
#     items = []
#     visit = []
#     visit_1 = []
#     id = 0
#     for i in range(500):
#         normalized_code1 = normalize_code(data[i]['focal_method'])
#         if normalized_code1 not in visit :
#             visit.append(normalized_code1)
#             # ds = []
#             # # visit_1 = []
#             # for j in range(len(data[i]['ds_generates'])):
#             #     normalized_code2 = normalize_code(data[i]['ds_generates'][j]['code'])
#             #     if normalized_code2 == normalized_code1 or normalized_code2 in visit_1:
#             #         continue
#             #     visit_1.append(normalized_code2)
#             #     ds.append(data[i]['ds_generates'][j])
#             ast = []
#             # visit_1 = []
#             for j in range(len(data[i]['ast_generates'])):
#                 normalized_code2 = normalize_code(data[i]['ast_generates'][j]['code'])
#                 if normalized_code2 == normalized_code1 or normalized_code2 in visit_1:
#                     continue
#                 visit_1.append(normalized_code2)
#                 ast.append(data[i]['ast_generates'][j])
#             items.append({
#                 "id" : id,
#                 "focal_method" : data[i]['focal_method'],
#                 "project" : data[i]['project'],
#                 "bug_num" : data[i]['bug_num'],
#                 "test_name" : data[i]['test_name'],
#                 "prefix" : data[i]['prefix'],
#                 "main_method_path" : data[i]['main_method_path'],
#                 "extracted_class_name" : data[i]['extracted_class_name'],
#                 "extracted_method_name" : data[i]['extracted_method_name'],
#                 # "ds_generates" : ds,
#                 "ast_generates" : ast,
#             })
#             id += 1

#     # sum = 0
#     # for i in range(len(items)):
#     #     sum += len(items[i]['ds_generates'])
#     # print(sum)
#     sum = 0
#     for i in range(len(items)):
#         sum += len(items[i]['ast_generates'])
#     print(sum)
#     with open('result_bug_inject.json', 'w', encoding='utf-8') as f:
#             json.dump(items, f, ensure_ascii=False, indent=2)