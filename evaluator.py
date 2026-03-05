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

# 1. 结合好items和generation 
CWD = "/home/ubuntu/myren/SF110"
path = "/home/ubuntu/myren/SF110"
items_path = "/home/ubuntu/myren/SF110/qwen_test.json"      # 替换为easyR1的数据集path     #替换为模型生成的path
COMPILE_TIMEOUT = 60 
RUN_TIMEOUT = 20
reward_assignment = {
    "kill_mutation" : 5,
    "manslaughter" : -5,
    "varies" : 1,
    "have_connection" : 2,
    "no_connection" : -2,
    "no_meaning" : -5,
}
def _has_connection(assertion: str, focal_method: str, test_prefix: str = "") -> bool:
    """
    判断断言与目标方法是否存在联系性 (+2 / -2 策略)
    增加对 test_prefix 的符号表解析
    """
    # --- 1. 从 Focal Method 提取核心资产 ---
    # 提取方法名 (增强对构造函数的支持)
    method_name = ""
    # 尝试匹配带返回类型的 standard 方法
    # 优化正则：避免 greedy 匹配吞掉方法名，并在无法匹配返回类型时尝试匹配构造函数模式
    name_match = re.search(r'(?:public|private|protected|static|final|\s)*[\w<>\[\]]+\s+(\w+)\s*\(', focal_method)
    if name_match:
        method_name = name_match.group(1)
    else:
        # 备选：匹配构造函数 (public ClassName(...) )
        ctor_match = re.search(r'(?:public|private|protected)\s+(\w+)\s*\(', focal_method)
        if ctor_match:
            method_name = ctor_match.group(1)

    # 提取类成员变更 (状态变量)
    # 匹配 ++var, var =, this.var, var.put()
    state_vars = set(re.findall(r'(\w+)(?:\s*[+\-*/]?=|\+\+|--|(?:\.put|\.append|\.add)\()', focal_method))
    # 过滤掉常见的循环变量
    state_vars = {v for v in state_vars if v not in {'i', 'j', 'it', 'entry', 'builder', 'k', 'n'}}

    # --- 2. 从 Test Prefix 提取实例名和中间变量 ---
    # 提取实例创建, 如 Location loc = new Location() -> loc
    # 提取中间赋值, 如 int int0 = loc.getY() -> int0
    prefix_vars = set(re.findall(r'(?:[\w<>\[\]]+)\s+(\w+)\s*=', test_prefix))
    
    # --- 3. 建立语义检查池 ---
    # 如果断言包含这些，则认为有联系
    check_set = state_vars | prefix_vars
    if method_name:
        check_set.add(method_name)
    
    # 常见的返回值占位符
    check_set.update({'result', 'res', 'output', 'actual', 'expected'})

    # --- 4. 判定逻辑 ---
    # A. 基础单词匹配
    for item in check_set:
        # 优化：使用 boundary 匹配，避免匹配到子串 (例如 'id' 匹配 'void')
        if re.search(rf'\b{re.escape(item)}\b', assertion):
            return True

    # B. 模糊语义关联 (Getter/Setter 匹配)
    # 比如 focal 中有 x，断言中有 getX()
    for var in state_vars:
        if not var: continue
        # 优化：正确处理驼峰命名 (capitalize() 会把 'myVal' 变成 'Myval', 应该是 'MyVal')
        camel_var = var[0].upper() + var[1:] if len(var) > 0 else ""
        if f"get{camel_var}" in assertion or f"is{camel_var}" in assertion:
            return True

    # C. 字面量关联 (如果断言使用了 prefix 中出现的数字/字符串)
    # 提取数字常量
    literals = set(re.findall(r'\b\d+\b', test_prefix))
    # 优化：增加字符串字面量提取
    string_lits = set(re.findall(r'"([^"]+)"', test_prefix))
    literals.update(string_lits)

    for lit in literals:
        if len(lit) > 1: # 忽略 0, 1 等极短数字/单字符
            # 优化：使用 boundary 检查，解决断言中数字无空格前缀无法匹配的问题
            # 或者是字符串直接包含
            if re.search(rf'\b{re.escape(lit)}\b', assertion):
                return True
            # 对于非数字字符串，直接检查包含性可能更稳健 (因为 assert "Expected" 里的内容可能不带引号)
            if not lit.isdigit() and lit in assertion:
                return True

    return False

def _is_trivial(assert_str: str) -> bool:
    if not assert_str: return True
    # 对应图中“琐碎断言”如 assertTrue(true)
    trivial_patterns = [
        r'assertTrue\s*\(\s*true\s*\)', 
        r'assertFalse\s*\(\s*false\s*\)', 
        r'assertNull\s*\(\s*null\s*\)',
        # 针对 new 出来的对象做非空检查，通常是无意义的
        r'assertNotNull\s*\(\s*new\s+',
        # 针对字符串字面量做非空检查
        r'assertNotNull\s*\(\s*".*"\s*\)',
        # 注意：这里不再无脑封杀 assertNotNull(变量)，因为有效的 assertNotNull 也会被扼杀。
        # 取而代之的是，如果它没能杀死任何变异体会受到额外的防作弊惩罚。
        # 匹配简单的自比较: assertEquals(x, x), assertEquals(1, 1)
        r'assertEquals\s*\(\s*([\w\d\.]+)\s*,\s*\1\s*\)',
        r'assertSame\s*\(\s*([\w\d\.]+)\s*,\s*\1\s*\)',
        r'assertNotSame\s*\(\s*([\w\d\.]+)\s*,\s*\1\s*\)'
    ]
    return any(re.search(p, assert_str) for p in trivial_patterns)

def reward_function(focal_method_output='FAIL', variant_output='FAIL', num_variants=1):
    """
    按照图片策略进行打分：
    1. 计算原始得分 (R_raw)
    2. 计算理论最大值 (R_max) 和 最小值 (R_min)
    3. 归一化得分 (R_norm)
    4. 映射到 [-1, 1] 区间 (R_final)
    """
    # --- 2. 策略打分 (R_raw) ---
    r_raw = 0
    
    # 理论最小值定义
    r_min_val = num_variants * (-5) - 7

    # A. 断言导致编译失败或 focal_method_output == FAIL
    if focal_method_output == "FAIL":
        return r_min_val # 直接返回最小值

    # B. 杀死变体 (PASS/FAIL)
    if focal_method_output == "PASS" and variant_output == "FAIL":
        r_raw += 5
    # C. 双方都通过，断言太弱 (PASS/PASS)
    elif focal_method_output == "PASS" and variant_output == "PASS":
        r_raw += -3

    return r_raw

def normalization(reward,n,focal_method,test_prefix,assertion,num):

    if num == 0: 
        if _is_trivial(assertion) or "assertNotNull" in (assertion or ""):
             return -0.8
        else:
             return -0.4

    # D. 断言精确性 (与方法本身联系)
    reward += reward_assignment['varies'] * num
    if _has_connection(assertion,focal_method,test_prefix):
        reward += reward_assignment['have_connection']
    else:
        reward += reward_assignment['no_connection']

    # E. 琐碎断言惩罚
    if _is_trivial(assertion or ""):
        reward += reward_assignment['no_meaning']
    
    # --- 3. 归一化计算 (Normalization) ---
    # n 代表变体数量
    R_max = n * 6 + 2
    R_min = n * (-5) - 7
    
    # 限制 r_raw 不超出理论边界
    reward = max(R_min, min(R_max, reward))
    
    # R_norm = (R_raw - R_min) / (R_max - R_min)
    r_norm = (reward - R_min) / (R_max - R_min)
    
    # R_final = 2 * R_norm - 1
    r_final = 2 * r_norm - 1
    
    return r_final

def optimized_compile(cwd, source_files, timeout=COMPILE_TIMEOUT):
    """
    直接调用 javac 编译指定文件列表，无需扫描整个项目。
    如果失败，返回 None (超时/异常)，或 CompletedProcess (含返回码)。
    """
    import signal
    try:
        # 1. 基础环境配置
        JAVA_HOME = os.getenv('JAVA_HOME', '/home/ubuntu/.conda/envs/rmy_llama/lib/jvm')
        JAVAC = os.path.join(JAVA_HOME, 'bin', 'javac')
        
        # 2. 构造 Classpath
        # 包含了 compile.sh 中的所有关键路径
        LIB_BASE = "/home/ubuntu/myren/SF110/lib"
        cp_segments = [
            f"{LIB_BASE}/evosuite.jar",
            f"{LIB_BASE}/junit-4.13.2.jar",
            f"{LIB_BASE}/hamcrest-core-1.3.jar",
            "test-lib/*",  # 项目自身的测试依赖
            "lib/*",       # 项目自身的依赖
            "/usr/share/ant/lib/*", # ANT 依赖
            ".",
            "target/classes",      # 自身类
            "target/test-classes"  # 测试类
        ]
        classpath = ":".join(cp_segments)
        
        # 3. 基础编译参数 (对齐 fast_compile.sh)
        base_cmd = [JAVAC, "-g:lines", "-nowarn", "-proc:none", "-encoding", "UTF-8", "-cp", classpath]
        
        for src_file in source_files:
            if not src_file: continue
            
            # [Fix] 只处理 src/main 和 evosuite-tests，明确跳过 src/test (与 fast_compile.sh 保持一致)
            if "src/test" in src_file:
                continue

            dest_dir = "target/classes"
            if "evosuite-tests" in src_file:
                dest_dir = "target/test-classes"
                
            cmd = base_cmd + ["-d", dest_dir, src_file]
            
            # [Fix] 这里的超时处理对齐 run_cmd_with_timeout
            # 使用 Popen + setsid + killpg 防止僵尸进程
            try:
                with subprocess.Popen(
                    cmd, 
                    cwd=cwd, 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.PIPE, 
                    text=True,
                    preexec_fn=os.setsid
                ) as process:
                    try:
                        _, stderr = process.communicate(timeout=timeout)
                        if process.returncode != 0:
                            # 编译失败，将 stderr 传回以便上层判断 (比如排查语法错误)
                            return subprocess.CompletedProcess(cmd, process.returncode, stderr=stderr)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except: pass
                        return None # 超时返回 None，触发 fallback
            except Exception:
                return None
        
        # 全部成功
        return subprocess.CompletedProcess(args=[], returncode=0)
        
    except Exception as e:
        print(f"[WARN] 快速编译异常: {e}, 将回退到脚本模式")
        return None

with open(items_path,"r",encoding="utf-8") as f:
    data = json.load(f)
generation = []
with open("qwen2.5_1.5_after_generated_predictions.jsonl","r",encoding="utf-8") as f:
    for line in f:
        pred = json.loads(line.strip())
        generation.append(strip_java_guard(pred['predict']))
gen_count = 0
for i in range(len(data)):
    if len(data[i]['ast_generates']) != 0:
        data[i]['predict'] = generation[gen_count]
        gen_count += 1

print(gen_count)


# 1. 替换

for i in range(len(data)):
    print(f"Processing item {i+1}/{len(data)}")
    reward = 0
    n = 0
    bug_varies = set()
    # assertion = '''assertEquals("数组长度应与nodeIds大小相同", sBMLGraphReader2_0.nodeIds.size(), intArray0.length);'''
    assertion = data[i]['predict']
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
            # 根据解析结果设置 reward：
            if summary.get("ok") is True:
                variant_output = "PASS"
            elif summary.get("ok") is False:
                variant_output = "FAIL"
                if bug_type not in bug_varies:
                    bug_varies.add(bug_type)
            else:
                if test_ret.returncode != 0:
                    variant_output = "FAIL"
                    if bug_type not in bug_varies:
                        bug_varies.add(bug_type)
                else:
                    variant_output = "PASS"
            n += 1
            reward += reward_function(focal_method_output,variant_output,n)

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
    
    
with open("excution_qwen2.5_1.5_after_generated_predictions.json","w",encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
