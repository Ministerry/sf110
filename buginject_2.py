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
COMPILE_TIMEOUT = 60 # 编译给予充足时间，防止因机器负载导致的误判
RUN_TIMEOUT = 20     # 运行时间设短一点，快速捕捉死循环，且足够覆盖 JVM 启动时间

# [优化] Python 直接编译函数，绕过 bash 和 find 扫描开销
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

if __name__ == '__main__':
    start_idx = int(sys.argv[1])
    end_idx = int(sys.argv[2])
    print(start_idx,end_idx)
    with open("assembled_2.json","r",encoding="utf-8") as f:
        data = json.load(f)
    result = []
    for i in range(start_idx, end_idx):
        print(f"Processing item {i+1}/{len(data)}")
        if i % 200 == 0:
            with open(f'bug_inject_{start_idx}.json', 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
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
            replace_from_first_brace(temp_test_method_path, test_code, f"{bug_num}_{project}")

            ast_generates = data[i].get('ast_generates', [])
            
            # 策略：数据平衡 (Data Balancing)
            # 修改：动态验证。先打乱顺序，然后验证直到该类型的有效数量达到上限
            random.shuffle(ast_generates)
            from collections import defaultdict
            LIMIT_PER_TYPE = 5 
            valid_type_counts = defaultdict(int)
            
            # 更新 data[i] 以便后续只保存保留下来的变体
            valid_variants = []
            survived_variants = []
            
            for variant in ast_generates:
                b_type = variant.get('bug_type', 'General')
                # 如果当前类型的有效变体已达标，则跳过后续验证
                if valid_type_counts[b_type] >= LIMIT_PER_TYPE:
                    continue
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
                
                # 1. 编译检查 (优先尝试 Python 极速编译)
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
                    cwd=project_path,
                    timeout=RUN_TIMEOUT
                )
                
                # 修改：包含超时的情况 (run_ret is None)
                if run_ret is None or run_ret.returncode != 0:
                    # --- 双重验证逻辑 Start ---
                    # 现在的失败/超时可能是 Prefix 本身导致的。如果是这样，无论 Assertion 写什么都会“Kill”，这属于噪音。
                    # 我们需要验证：在由“Prefix”单独组成的测试中，变体是否能存活？
                    
                    print("  > 检测到潜在 Bug，正在验证是否为 Prefix 导致...")
                    
                    # 1. 临时构造只包含 Prefix 的测试代码
                    prefix_only_code = "public void test0()  throws Throwable {\n" + prefix + "\n}"
                    replace_from_first_brace(temp_test_method_path, prefix_only_code, f"{bug_num}_{project}")
                    
                    # 2. 必须重新编译 Test (因为测试内容变了)
                    # 极速模式：只重新编译 Test 文件
                    check_compile = optimized_compile(project_path, [relatest_path])
                    if check_compile is None or check_compile.returncode != 0:
                        check_compile = run_cmd_with_timeout(['bash', 'fast_compile.sh'], cwd=project_path, timeout=COMPILE_TIMEOUT)
                    
                    is_prefix_failure = False
                    if check_compile and check_compile.returncode == 0:
                        # 3. 运行纯 Prefix 测试
                        check_run = run_cmd_with_timeout(['bash', 'run.sh', test_class], cwd=project_path, timeout=RUN_TIMEOUT)
                        if check_run is None or check_run.returncode != 0:
                             is_prefix_failure = True
                             print(f"  > [验证结果] Prefix 测试失败/超时。变体为不可用噪音。")
                        else:
                             print(f"  > [验证结果] Prefix 测试通过。确认是 Assertion 触发了 Bug。")
                    else:
                        is_prefix_failure = True # 编译都挂了，视为噪音
                    
                    # 4. 无论结果如何，必须还原测试代码 (含 Assertion)，以免影响后续逻辑或下一次循环
                    replace_from_first_brace(temp_test_method_path, test_code, f"{bug_num}_{project}")
                    # 注意：还原后无需立即编译，因为下一次循环开始时会先 Compile Subject 再 Run
                    
                    if not is_prefix_failure:
                        # 编译通过 + (测试失败 或 超时) + (Prefix 单独运行通过) = 真正的高质量 Bug
                        variant['pass'] = 1
                        valid_variants.append(variant)
                        valid_type_counts[b_type] += 1
                    else:
                        # Prefix 就挂了，跳过
                         variant['pass'] = 0
                    # --- 双重验证逻辑 End ---
                else:
                    print("变体通过了测试(等价变体)，将被归入 survived_generates 以供 RL 探索")
                    variant['pass'] = 0 
                    survived_variants.append(variant)

                if backup_file and temp_main_method_path and os.path.exists(temp_main_method_path):
                    restore_java_file_from_content(temp_main_method_path, backup_file)
            
            # 只保存验证过的有效变体
            data[i]['ast_generates'] = valid_variants
            data[i]['survived_generates'] = survived_variants
            result.append(data[i])
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            print(f"[ERROR] 异步计算异常: {str(e)}")
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    with open(f'bug_inject_{start_idx}.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2) 