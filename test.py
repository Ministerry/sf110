import os
import json
import pandas as pd
import shutil
import re
import subprocess
import javalang
from filelock import FileLock
import time
import traceback
import tempfile
import argparse
import datetime
import json
from typing import Optional
from utils import *
from examples.reward_function.train_utils import *
import sys

# 1. 结合好items和generation 
CWD = "/home/ubuntu/myren/SF110"
path = "/home/ubuntu/myren/SF110"
items_path = "/home/ubuntu/myren/SF110/result_sf110.json"      # 替换为easyR1的数据集path     #替换为模型生成的path

with open(items_path,"r",encoding="utf-8") as f:
    data = json.load(f)

# 1. 替换

for i in range(len(data)):
    print(f"Processing item {i+1}/{len(data)}")
    reward = 0
    n = 0
    bug_varies = set()
    # assertion = '''assertEquals("数组长度应与nodeIds大小相同", sBMLGraphReader2_0.nodeIds.size(), intArray0.length);'''
    assertion = data[i]['ds_generates'][0]
    print(assertion)
    # assertion = extra_info.get('predict', '')
    try:
        # 1. 提取基础信息
        extra_info = data[i]
        focal_method_output = None
        id =extra_info.get('id', '')
        prefix = extra_info.get('prefix', '')
        project = extra_info.get('project', '')
        bug_num = extra_info.get('bug_num', '')
        main_method_path = extra_info.get('main_method_path', '')
        extracted_class_name = extra_info.get('extracted_class_name', '')
        extracted_method_name = extra_info.get('extracted_method_name', '')
        test_method_path = extra_info.get('test_method_path', '')
        test_name = extra_info.get('test_name', '')
        focal_method = extra_info.get('focal_method', '')
        dir_name = f"{bug_num}_{project}"
        src_project_path = os.path.join(CWD, dir_name)
        temp_dir = tempfile.mkdtemp(prefix=f"reward_{bug_num}_{project}_")
        project_path = os.path.join(temp_dir, dir_name)
        
        # 优化：使用 hardlink 复制项目，加速 IO
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
        ast_generates = extra_info.get("ast_generates", [])
        # assertion = extra_info.get('assert', '')
        test_code = "public void test0()  throws Throwable { \n " + prefix + "\n" + assertion + "\n}"
        # test_code = extra_info.get('focal_prefix', '')
        # 1. 生成断言后对原方法进行测试
        replace_from_first_brace(temp_test_method_path, test_code, f"{bug_num}_{project}")
        test_ret = optimized_compile(project_path, [relative_path, relatest_path])
        
        # 如果极速编译失败 (返回 None 或 returncode != 0)，回退到 fast_compile.sh
        if test_ret is None or test_ret.returncode != 0:
            test_ret = run_cmd_with_timeout(
                ['bash', 'fast_compile.sh'],
                cwd=project_path,
                timeout=COMPILE_TIMEOUT
            )
        if test_ret is None or test_ret.returncode != 0:
            data[i]['reward'] = -1.0
            print("编译失败")
            with open(temp_test_method_path,"r",encoding='utf-8') as f:
                content = f.read()
            print(content)
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            continue
        test_class = test_name + 'EvoSuiteTest'
        test_ret = run_cmd_with_timeout(
            ['bash', 'run.sh', test_class],
            cwd=project_path
        )
        out_text = ""
        if test_ret is None:
            # 超时或异常
            data[i]['reward'] = -1.0
            print("运行失败")
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            continue
        else:
            out_text = (test_ret.stdout or "") + "\n" + (test_ret.stderr or "")

        # 解析 JUnit 输出
        summary = parse_junit_output(out_text)
        focal_method_output = "PASS"
        if summary.get("ok") is False:
            reward_function("FAIL")
            data[i]['reward'] = -1.0
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            continue

    except Exception as e:
        print(f"[ERROR] 异步奖励计算异常: {str(e)}")
    finally:
        if temp_dir and os.path.exists(temp_dir) and focal_method_output == None:
            shutil.rmtree(temp_dir, ignore_errors=True)
            
    for j in range(len(ast_generates)):
        try : 
            backup_file = None
            variant_output = None
            code = ast_generates[j].get('code', '')
            bug_type = ast_generates[j].get('bug_type', '')
            # 3. 创建独立临时目录，复制项目副本（核心隔离手段）

            if not os.path.exists(temp_main_method_path):
                print(f"[ERROR] 临时目录中目标文件不存在: {temp_main_method_path}")
                continue
            # 5. 分别替换并运行变体 + assert
            replace_result = replace_method_in_java_file(
                temp_main_method_path,
                extracted_class_name,
                extracted_method_name,
                code
            )
            backup_file = replace_result.get('backup_file')
            if not replace_result["success"]:
                print(f"[ERROR] 替换方法失败: {replace_result['error']}")
                continue         
            
            # 6. 运行编译和测试
            test_ret = optimized_compile(project_path, [relative_path, relatest_path])
            
            # 如果极速编译失败 (返回 None 或 returncode != 0)，回退到 fast_compile.sh
            if test_ret is None or test_ret.returncode != 0:
                test_ret = run_cmd_with_timeout(
                    ['bash', 'fast_compile.sh'],
                    cwd=project_path,
                    timeout=COMPILE_TIMEOUT
                )
            if test_ret is None or test_ret.returncode != 0:
                print("变体编译失败")
                continue
            
            test_class = test_name + 'EvoSuiteTest'
            test_ret = run_cmd_with_timeout(
                ['bash', 'run.sh', test_class],
                cwd=project_path
            )
            
            out_text = ""
            if test_ret is None:
                # 超时或异常
                print("变体运行失败")
                continue
            else:
                out_text = (test_ret.stdout or "") + "\n" + (test_ret.stderr or "")
                
            # 解析 JUnit 输出
            summary = parse_junit_output(out_text)
            
            # 使用更细粒度的失败类型解析
            failure_type = None
            if summary.get("ok") is True:
                variant_output = "PASS"
            else:
                variant_output = "FAIL"
                
                # 判定逻辑优化：
                # 1. 如果明确看到了 AssertionError 的堆栈信息 -> 逻辑错误 (Logic Kill)
                # 2. 如果看到了 failures > 0 且 errors == 0 -> 逻辑错误 (通常 JUnit 将断言失败计为 failures)
                # 3. 其他情况 (errors > 0, 或者看到 Exception) -> 崩溃/异常 (Crash Kill)
                
                is_assertion_error = "java.lang.AssertionError" in out_text
                has_failures = re.search(r'Failures:\s*[1-9]', out_text)
                has_errors = re.search(r'Errors:\s*[1-9]', out_text)
                
                if is_assertion_error:
                    failure_type = "assertion"
                elif has_failures and not has_errors:
                    failure_type = "assertion"
                else:
                    failure_type = "exception"

                if bug_type not in bug_varies:
                    # 对于变异测试，只有真正触发变异(导致失败)才是有意义的
                    bug_varies.add(bug_type)

            # 特殊情况：如果是超时或者 JVM 崩溃，test_ret.returncode != 0 但 junit 可能没解析出来
            if variant_output == "FAIL" and summary.get("ok") is None:
                 failure_type = "exception"

            n += 1
            reward += reward_function(focal_method_output, variant_output, failure_type)

        except Exception as e:
            print(f"[ERROR] 异常: {str(e)}")
        finally:
            # 10. 清理资源（无论成功失败都恢复+删除临时文件）
            if backup_file and temp_main_method_path and os.path.exists(temp_main_method_path):
                restore_java_file_from_content(temp_main_method_path, backup_file)
                    
    if temp_dir and os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)    
    
    if n == 0:
        if focal_method_output == "PASS":
            data[i]['reward'] = -0.5
        else:
            data[i]['reward'] = -1.0
    elif n != 0:
        data[i]['reward'] = normalization(reward,n,focal_method,prefix,assertion,len(bug_varies))
    
    
with open(f"excution_deepseek_generated_predictions.json","w",encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
