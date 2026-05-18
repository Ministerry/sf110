import os
import json
import pandas as pd
import shutil
import re
import subprocess
import tempfile
import json
import scipy.stats as stats
from utils import *
from train_utils import *
import sys
from soot_dynamic_utills import *
# 1. 结合好items和generation 
CWD = "/home/ubuntu/myren/SF110"
path = "/home/ubuntu/myren/SF110"
items_path = "/home/ubuntu/myren/SF110/test_deepseek.json"      # 替换为easyR1的数据集path     #替换为模型生成的path
def calculate_assertion_reward(assertion, is_exception_expected, summary, extra_info, out_text, static_quality_multiplier, main_method_path, test_method_path, src_project_path, project_path, soot_analysis=None, dynamic_analysis=None):
    reward = 0.0
    prefix = extra_info.get('prefix', '')
    if is_exception_expected:
        # --- 异常捕获奖励逻辑 (对齐 evaluator 的优化思路) ---
        # 1. 提取预期异常类型
        expected_exception = "Exception" 
        match = re.search(r'// Undeclared exception!\s*(\w+)', prefix)
        if match:
            expected_exception = match.group(1)
        
        # 2. 详细断言奖励 (提取 caught 后的验证逻辑)
        detail_bonus = 0.0
        if re.search(r'\.\s*getMessage\s*\(', assertion):
            detail_bonus = 0.15

        if summary.get("ok") is True:
            # 3. 检查异常精准度
            precision_multiplier = 1.0
            if expected_exception not in assertion and expected_exception != "Exception":
                precision_multiplier = 0.7 
            
            # Base Reward 同步 eval.py 调整为 0.5，结合静态乘数和精准度乘数
            # Only grant a meaningful base reward when semantic/static analysis justifies it.
            # Otherwise give a small or zero base to avoid rewarding trivial passing tests.
            base_reward = 0.0
            semantic_ok = False
            try:
                # try to compute a semantic score if analysis is available
                if soot_result and dyn_result and 'error' not in dyn_result:
                    q_scores = score_assertion_from_statement(assertion, soot_result, dyn_result)
                    if isinstance(q_scores, list) and len(q_scores) > 0:
                        q_scores = q_scores[0]
                    semantic_total = q_scores.get('total_score', 0.0) if isinstance(q_scores, dict) else 0.0
                    # also allow fallback on syntactic connection
                    connection = False
                    try:
                        connection = _has_connection(assertion, extra_info.get('focal_method', ''), prefix)
                    except Exception:
                        connection = False
                    if semantic_total >= SEMANTIC_THRESHOLD or connection:
                        semantic_ok = True
            except Exception:
                semantic_ok = False

            if semantic_ok:
                base_reward = BASE_REWARD_SCALE * static_quality_multiplier * precision_multiplier
            else:
                # penalize or zero out trivial but passing assertions
                base_reward = 0.0
            
            # --- [引入] 4D 覆盖率深度评价 ---
            try:
                import sys
                if "/home/ubuntu/myren/SF110" not in sys.path:
                    sys.path.insert(0, "/home/ubuntu/myren/SF110")
                from soot_dynamic_utills import analyze_variable_flow, perform_dynamic_analysis, score_assertion_from_statement
                
                soot_result = soot_analysis or extra_info.get('soot_analysis_result') or analyze_variable_flow(extra_info)
                dyn_result = dynamic_analysis or extra_info.get('dynamic_analysis')
                
                if not dyn_result:
                    dyn_rel_main = os.path.relpath(main_method_path, src_project_path)
                    dyn_rel_test = os.path.relpath(test_method_path, src_project_path)
                    dyn_result = perform_dynamic_analysis(extra_info, project_path, dyn_rel_main, dyn_rel_test)

                    if soot_result and dyn_result and 'error' not in dyn_result:
                        q_scores = score_assertion_from_statement(assertion, soot_result, dyn_result)
                        if isinstance(q_scores, list) and len(q_scores) > 0:
                            q_scores = q_scores[0]

                        breakdown = q_scores.get('breakdown', {}) if isinstance(q_scores, dict) else {}
                        cf_val = breakdown.get('Role', 0) + breakdown.get('Complexity', 0)
                        mod_val = breakdown.get('Modification', 0)

                        # Extra Reward 同步 eval.py，归一化基数调整为30.0，上限0.5
                        weighted_4d = (cf_val * 0.7 + mod_val * 0.3)
                        extra_reward = min(weighted_4d / 30.0, 0.5)

                        # Only add semantic extra reward when semantic_ok
                        if semantic_ok:
                            reward = base_reward + extra_reward + detail_bonus
                        else:
                            reward = 0.0 + detail_bonus
                    else:
                        reward = base_reward
                else:
                    reward = base_reward
            except:
                reward = base_reward
        else:
            # 运行失败
            # 优化：针对异常场景，如果捕获了预期异常但 JUnit 依然报错（如 JVM 退出），给予一定的“拦截成功”分
            is_exception_in_log = expected_exception in out_text or "SystemExitException" in out_text
            
            if is_exception_in_log:
                reward = 0.3 * static_quality_multiplier
            else:
                reward = 0.05 if "java.lang.AssertionError" in out_text else (0.1 * static_quality_multiplier)
    else:
        # --- 正常断言奖励逻辑 ---
        if summary.get("ok") is False:
            # 阶梯奖励：测试未能通过
            reward = 0.0
        else:
            # ------------------- 使用 Soot + Dynamic 分析进行打分评估 -------------------
            import sys
            if "/home/ubuntu/myren/SF110" not in sys.path:
                sys.path.insert(0, "/home/ubuntu/myren/SF110")
                
            try:
                from soot_dynamic_utills import analyze_variable_flow, perform_dynamic_analysis, score_assertion_from_statement
                
                soot_result = soot_analysis or extra_info.get('soot_analysis_result') or analyze_variable_flow(extra_info)
                dyn_result = dynamic_analysis or extra_info.get('dynamic_analysis')
                
                if not dyn_result:
                    dyn_rel_main = os.path.relpath(main_method_path, src_project_path)
                    dyn_rel_test = os.path.relpath(test_method_path, src_project_path)
                    dyn_result = perform_dynamic_analysis(extra_info, project_path, dyn_rel_main, dyn_rel_test)

                if soot_result and dyn_result and 'error' not in dyn_result and 'error' not in (soot_result if isinstance(soot_result, dict) else {}):
                    score_summary = score_assertion_from_statement(assertion, soot_result, dyn_result)
                    total_score = score_summary.get('total_score', 0.0)
                    
                    # [优化] 只有包含业务关键词的断言才能从 Soot 静态分析中获得正常收益
                    if any(kw in assertion.lower() for kw in ['get', 'is', 'has', 'equals', 'contains']):
                        # 同步评估脚本：分母统一为 60.0，上限封顶 0.5 (与异常断言 extra_reward 逻辑一致)
                        reward = min(total_score / 60.0, 0.5) 
                    else:
                        # 否则，即便覆盖了变量流，由于缺乏业务语义，分值减半并设上限
                        reward = min(total_score / 120.0, 0.05)
                else:
                    # [修改] 原本编译通过给 0.2，现在如果拿不到分析分，只给 0.0 避免苟活得分
                    reward = 0.0

            except ImportError as e:
                reward = -1.0
            except Exception as e:
                reward = -0.3

    # 统一增加惩罚机制：防刷分漏洞
    if reward >= 0.0:
        is_hacking = False
        
        # --- 漏洞检测规则库 ---
        # 1. 检查自比较，如 assertEquals(a, a)
        if re.search(r'assert(?:Equals|Same|NotEquals)\s*\(\s*([^,\s]+)\s*,\s*\1\s*\)', assertion, re.IGNORECASE):
            is_hacking = True
        # 2. 检查脱裤子放屁的 boolean 包装：assertEquals(expr != null, true)
        elif re.search(r'(!=|==)\s*null\s*,\s*(true|false)', assertion, re.IGNORECASE):
            is_hacking = True
        # 3. 检查把 true/false 作为参数传给 assertEquals：assertEquals(expr, true)
        elif re.search(r'assertEquals\s*\([^,]+,\s*(true|false)\s*\)', assertion, re.IGNORECASE) or \
             re.search(r'assertEquals\s*\(\s*(true|false)\s*,', assertion, re.IGNORECASE):
            is_hacking = True
        # 4. 检查纯废话断言
        elif re.search(r'assertTrue\s*\(\s*true\s*\)', assertion, re.IGNORECASE) or \
             re.search(r'assertFalse\s*\(\s*false\s*\)', assertion, re.IGNORECASE):
            is_hacking = True
        # 5. [新增] 封杀 instanceof 类型判断作弊 (排除针对异常 e 的合理探索)
        elif "instanceof" in assertion and not is_exception_expected:
            is_hacking = True
        # 6. [新增] 封杀内部进行无意义的 boolean 字面量计算，如 assertTrue(boolean0 == false)
        elif re.search(r'assert(?:True|False)\s*\(\s*[a-zA-Z0-9_]+\s*(?:==|!=)\s*(?:true|false)\s*\)', assertion, re.IGNORECASE):
            is_hacking = True
        # 7. [针对8B新增] 封杀脱裤子放屁形式的直接判空与非空判断作弊（但允许原生 assertNotNull）
        elif re.search(r'assert(?:True|False)\s*\(.*(?:!=|==)\s*null\s*\)', assertion, re.IGNORECASE):
            is_hacking = True
        # 8. 封杀字符串字面量自等作弊，例如: assertTrue("x", "x".equals("x")); 或 assertEquals("x","x")
        elif re.search(r'assert(?:True|False)\s*\(\s*"([^\"]+)"\s*,\s*"\\1"\.equals\(\s*"\\1"\s*\)\s*\)', assertion):
            is_hacking = True
        elif re.search(r'assertEquals\s*\(\s*"([^\"]+)"\s*,\s*"\\1"\s*\)', assertion):
            is_hacking = True
        # 8. [针对8B新增] 去除对浅层形态判断(size/length)的直接负分惩罚，改为在后续业务奖励中不加分
        
        if is_hacking:
            # [惩罚升级] 针对 8B 这类能抗住大惩罚的大模型，重拳出击，直接 -1.0
            reward -= 1.0  
        else:
            # [奖励降级] 合法断言基础补助调低至 +0.01（原本为+0.05）
            valid_asserts = ["assertEquals", "assertSame", "assertNotEquals", "assertArrayEquals", 
                             "assertTrue", "assertFalse"]
            if any(kw in assertion for kw in valid_asserts):
                reward += 0.01
            
            # [业务语义奖励] 深度对象状态获取的行为探查
            business_call_match = re.search(r'\.(?:get|is|has|contains|equals|toString|matches)[A-Za-z0-9_]*\s*\(', assertion)
            if business_call_match:
                if re.search(r'\(\s*[^)\s]+\s*\)', assertion): # 带参数的复杂调用
                    reward += 0.5
                else:
                    reward += 0.3
            
            # [精准预测奖励] 鼓励模型预测具体业务数值（除 0, 1, true, false 外的特定值）
            # 对于 size/length 这类浅层 API 只比较 0，不给予高分鼓励，但不再给 -1 惩罚
            if re.search(r'(?:"[^"]{2,}"|\d{2,}|[A-Z_]{3,})', assertion) and not re.search(r'\.(?:length|size|isEmpty|count)\s*\(', assertion, re.IGNORECASE):
                 reward += 0.2


        # 长度惩罚：改为阶梯剧烈惩罚
        if len(assertion) > 100:
            reward -= 0.4
        elif len(assertion) > 70:
            reward -= 0.1
            
    # 【GRPO防畸变约束】 截断由于各种奖励叠加和惩罚导致的过度离谱分数
    # 避免极个别超高分/超低分主导同一回答组的 Standard Score 计算
    reward = max(min(reward, 1.5), -1.5)
            
    return reward

with open(items_path,"r",encoding="utf-8") as f:
    data = json.load(f)

skip_reward_eval = parse_bool_arg(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SKIP_REWARD_EVAL"), default=False)

for i in range(len(data)):
    print(f"Processing item {i+1}/{len(data)}")
    
    # --- Integrate Soot Analysis ---
    if 'soot_analysis_result' not in data[i]:
        try:
            soot_result = analyze_variable_flow(data[i])
            print(f"[Soot Analysis] Result: {json.dumps(soot_result)}")
            data[i]['soot_analysis_result'] = soot_result
        except Exception as e:
            print(f"[WARN] Soot Analysis Failed: {e}")
            data[i]['soot_analysis_result'] = {'error': str(e)}
    if 'dynamic_analysis' not in data[i]:
        focal_method_code = data[i].get('focal_method', '')
        extra_info = data[i]
        
        # Load ast_generates early
        ast_generates = extra_info.get("ast_generates", [])
        
        # Extract basic info needed for path construction
        project = extra_info.get('project', '')
        bug_num = extra_info.get('bug_num', '')
        dir_name_base = f"{bug_num}_{project}"
        main_method_path_base = extra_info.get('main_method_path', '')
        test_method_path_base = extra_info.get('test_method_path', '')
        src_project_path_base = os.path.join(CWD, dir_name_base)
        # Only need to check the base case once, not for every variant
        try:
            print(f"Running Dynamic Analysis for item {i}")
            # Need to setup project path temporarily
            dyn_temp_dir = tempfile.mkdtemp(prefix=f"dynamic_{bug_num}_{project}_")
            dyn_project_path = os.path.join(dyn_temp_dir, dir_name_base)
            
            # Copy project
            subprocess.run(['cp', '-al', src_project_path_base, dyn_project_path], check=True, stderr=subprocess.DEVNULL)
            # Handle target
            src_target = os.path.join(src_project_path_base, 'target')
            dst_target = os.path.join(dyn_project_path, 'target')
            if os.path.exists(dst_target): shutil.rmtree(dst_target)
            # Using copytree with dirs_exist_ok=True if python 3.8+ else ignore
            if os.path.exists(src_target): shutil.copytree(src_target, dst_target)
            
            # Paths
            dyn_rel_main = os.path.relpath(main_method_path_base, src_project_path_base)
            dyn_rel_test = os.path.relpath(test_method_path_base, src_project_path_base)
            
            # Unlink crucial files
            dyn_full_test = os.path.join(dyn_project_path, dyn_rel_test)
            if os.path.exists(dyn_full_test):
                with open(dyn_full_test, 'r') as f: c = f.read()
                os.unlink(dyn_full_test)
                with open(dyn_full_test, 'w') as f: f.write(c)

            dyn_result = perform_dynamic_analysis(data[i], dyn_project_path, dyn_rel_main, dyn_rel_test)
            data[i]['dynamic_analysis'] = dyn_result
            print(f"[Dynamic Analysis] Result: {json.dumps(dyn_result)}")
            shutil.rmtree(dyn_temp_dir, ignore_errors=True)
            
        except Exception as e:
            print(f"[WARN] Dynamic Analysis Failed: {e}")
            data[i]['dynamic_analysis'] = {'error': str(e)}
        finally:
            if 'dyn_temp_dir' in locals() and os.path.exists(dyn_temp_dir):
                shutil.rmtree(dyn_temp_dir, ignore_errors=True)

    # --- Quantify Assertions ---
    if 'soot_analysis_result' in data[i] and 'dynamic_analysis' in data[i] and 'error' not in data[i]['dynamic_analysis']:
        try:
             quantified_scores = quantify_assertion_value(data[i]['soot_analysis_result'], data[i]['dynamic_analysis'])
             data[i]['quantified_assertions'] = quantified_scores
             print(f"[Quantification] Scores: {json.dumps(quantified_scores, indent=2)}")
        except Exception as e:
             print(f"[WARN] Quantification Failed: {e}")
             data[i]['quantified_assertions'] = {'error': str(e)}

    # 初始化quantified_scores，确保有对应长度的空列表，避免KeyError
    if 'quantified_scores' not in data[i]:
        ds_gens = data[i].get('ds_generates', [])
        data[i]['quantified_scores'] = [{} for _ in range(len(ds_gens))]
        
    if 'ds_rewards' not in data[i]:
        ds_gens = data[i].get('ds_generates', [])
        data[i]['ds_rewards'] = [0 for _ in range(len(ds_gens))]
        
    if 'rewards' not in data[i]:
        ds_gens = data[i].get('ds_generates', [])
        data[i]['rewards'] = [0.0 for _ in range(len(ds_gens))]
        
    if 'reward_info' not in data[i]:
        ds_gens = data[i].get('ds_generates', [])
        data[i]['reward_info'] = [{} for _ in range(len(ds_gens))]

    if skip_reward_eval:
        continue
    
    for k in range(5):
        reward = 0
        n = 0
        bug_varies = set()
        # Initialize variables to avoid NameError if try block fails
        ast_generates = []
        temp_main_method_path = ""
        project_path = ""
        temp_dir = None
        focal_method_output = None
        backup_file = None
        
        extra_info = data[i]
        prefix = extra_info.get('prefix', '')
        is_exception_expected = "// Undeclared exception!" in prefix
  
        try:
            if k >= len(data[i].get('ds_generates', [])):
                print(f"Skipping index {k} as ds_generates has length {len(data[i].get('ds_generates', []))}")
                continue

            assertion = data[i]['ds_generates'][k]
            print(assertion)
            data[i]['quantified_scores'][k] = score_assertion_from_statement(assertion,data[i]['soot_analysis_result'], data[i]['dynamic_analysis'])
            # CFG based degree calculation for Focal Method
            
            if is_exception_expected:
                # 简单的正则提取 try { ... () ... } 括号中的调用对象
                # 这里的逻辑建议在 soot_dynamic_utils.py 中增强，目前我们尝试手动从 predict 提取 focal 调用
                potential_calls = re.findall(r'(\w+)\.\w+\(', assertion)
                if potential_calls:
                    # 重新针对 try 块内的对象进行一次评分匹配
                    inner_scores = []
                    for obj_name in set(potential_calls):
                        if obj_name in data[i].get('quantified_assertions', {}):
                            inner_scores.append(data[i]['quantified_assertions'][obj_name])
                    if inner_scores:
                        # 取最高分的那个对象作为代表
                        best_inner = max(inner_scores, key=lambda x: x.get('total_score', 0))
                        data[i]['quantified_scores'][k] = best_inner

            focal_method_code = data[i].get('focal_method', '')
            extra_info = data[i]
            
            # Load ast_generates early
            ast_generates = extra_info.get("ast_generates", [])
            
            # Extract basic info needed for path construction
            project = extra_info.get('project', '')
            bug_num = extra_info.get('bug_num', '')
            dir_name_base = f"{bug_num}_{project}"
            main_method_path_base = extra_info.get('main_method_path', '')
            test_method_path_base = extra_info.get('test_method_path', '')
            src_project_path_base = os.path.join(CWD, dir_name_base)
            
            id =extra_info.get('id', '')
            project = extra_info.get('project', '')
            bug_num = extra_info.get('bug_num', '')
            main_method_path = extra_info.get('main_method_path', '')
            extracted_class_name = extra_info.get('extracted_class_name', '')
            extracted_method_name = extra_info.get('extracted_method_name', '')

            # Assertion Complexity Analysis 

            dir_name = f"{bug_num}_{project}"
            test_method_path = extra_info.get('test_method_path', '')
            test_name = extra_info.get('test_name', '')
            focal_method = extra_info.get('focal_method', '')
            dir_name = f"{bug_num}_{project}"
            src_project_path = os.path.join(CWD, dir_name)
            temp_dir = tempfile.mkdtemp(prefix=f"reward_{bug_num}_{project}_")
            project_path = os.path.join(temp_dir, dir_name)

            # 1. 提取基础信息
            
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
                data[i]['ds_rewards'][k] = 0 # 原程序没通过，断言无效，记为 0 杀怪
                if isinstance(data[i]['quantified_scores'][k], dict):
                    data[i]['quantified_scores'][k]['total_score'] = 0 # 强行把无效断言的启发式打分清零
                print("原始代码+断言编译失败")
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
                data[i]['ds_rewards'][k] = 0
                if isinstance(data[i]['quantified_scores'][k], dict):
                    data[i]['quantified_scores'][k]['total_score'] = 0 # 强行把无效断言的启发式打分清零
                print("原始代码运行失败/超时")
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                continue
            else:
                out_text = (test_ret.stdout or "") + "\n" + (test_ret.stderr or "")

            # 解析 JUnit 输出
            summary = parse_junit_output(out_text)

                    # 使用统一打分函数
            reward, info = calculate_unified_reward(
                assertion, prefix, 
                data[i].get('soot_analysis_result', {}), 
                data[i].get('dynamic_analysis', {}), 
                summary, out_text
            )
            data[i]['rewards'][k] = reward
            data[i]['reward_info'][k] = info
            
            # 如果是异常场景，直接跳过变异测试
            if info['type'] == 'exception':
                print(f"异常场景结算: {info['status']}, Reward: {reward:.4f}, Details: {info}")
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                continue

            if summary.get("ok") is False:
                print("原始代码运行不通过 (假阳性断言，击杀无效)")
                data[i]['ds_rewards'][k] = 0
                if isinstance(data[i]['quantified_scores'][k], dict):
                    data[i]['quantified_scores'][k]['total_score'] = 0 # 强行把无效断言的启发式打分清零
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                continue
            
            focal_method_output = "PASS"

        except Exception as e:
            print(f"[ERROR] 异步奖励计算异常: {repr(e)}")
        finally:
            if temp_dir and os.path.exists(temp_dir) and focal_method_output == None:
                shutil.rmtree(temp_dir, ignore_errors=True)
                
        if focal_method_output != "PASS":
            continue
                
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
                    # 超时或异常 (比如死循环变异)，视为成功改变了程序的运行状态被断言或框架拦截 (Kill)
                    print("变体运行超时/崩溃 (视为 Kill)")
                    n += 1
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

                # >>> 核心计分修复: 当测试由于变异代码运行报 FAIL，此时才是拦截了 Bug <<<
                if variant_output == "FAIL":
                    n += 1

            except Exception as e:
                print(f"[ERROR] 异常: {str(e)}")
            finally:
                # 10. 清理资源（无论成功失败都恢复+删除临时文件）
                if backup_file and temp_main_method_path and os.path.exists(temp_main_method_path):
                    restore_java_file_from_content(temp_main_method_path, backup_file)
                        
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)    
    
        if not is_exception_expected:
            total_mutants = len(ast_generates)
            kill_rate = n / total_mutants if total_mutants > 0 else 0.0
            
            # 对于正常断言，奖励等于击杀率 (0.0 ~ 1.0)
            data[i]['ds_rewards'][k] = n
            print(f"变异体杀死数量 (断言): {n}/{total_mutants}, 击杀率: {kill_rate:.4f}")
        else:
            # 对于异常断言，保留之前基于覆盖率计算的 rewards (0.5 ~ 1.0)
            print(f"异常捕获评分 (基于4D覆盖率): {data[i].get('rewards', 0):.4f}")

    
output_name = "excution_deepseek_generated_predictions.json"
with open(output_name, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"[INFO] Wrote output to {output_name}")

# --- 皮尔逊相关系数分析 (Normal & Exception 场景) ---
stats_data = {
    "normal": {"heuristic_scores": [], "mutation_kills": []},
}

for item in data:
    ds_gens = item.get('ds_generates', [])
    quantified = item.get('quantified_scores', [])
    rewards = item.get('ds_rewards', [])
    
    if "// Undeclared exception!" in item.get('prefix', ''):
        continue
        
    total_mutants = len(item.get('ast_generates', []))
    if total_mutants == 0:
        continue

    for k in range(len(ds_gens)):
        if k < len(quantified) and k < len(rewards):
            q_score = quantified[k].get('total_score', 0.0) if isinstance(quantified[k], dict) else 0.0
            # 仅统计在原始程序上运行成功且分数大于0的有效断言，以获得更真实的逻辑相关性
            if q_score > 0:
                kill_rate = rewards[k] / total_mutants
                
                stats_data["normal"]["heuristic_scores"].append(q_score)
                stats_data["normal"]["mutation_kills"].append(kill_rate)

heuristic_scores = stats_data["normal"]["heuristic_scores"]
mutation_kills = stats_data["normal"]["mutation_kills"]

print("\n" + "="*40)
print("Pearson Correlation Analysis (Normal Assertions):")
if len(heuristic_scores) > 1:
    corr, p_value = stats.pearsonr(heuristic_scores, mutation_kills)
    print(f"Sample Size: {len(heuristic_scores)}")
    print(f"Correlation (Heuristic 4D Score vs Mutation Kill Rate): {corr:.4f}")
    print(f"P-value: {p_value:.4e}")
    
    # --- 集成分组分析与等级相关性分析 ---
    try:
        df_stats = pd.DataFrame({
            'score': heuristic_scores,
            'kill_rate': mutation_kills
        })
        
        # 1. Top-K 筛选能力分析
        k_size = max(1, int(len(df_stats) * 0.2)) # 取前20%作为高分组
        top_k = df_stats.nlargest(k_size, 'score')
        bottom_k = df_stats.nsmallest(k_size, 'score')
        
        print(f"\nTop 20% High Score Group - Avg Kill Rate: {top_k['kill_rate'].mean():.4f}")
        print(f"Bottom 20% Low Score Group - Avg Kill Rate: {bottom_k['kill_rate'].mean():.4f}")

        # 2. 命中率分析 (Hit Rate): 评分能否区分“完全无用”和“至少有用”的断言
        median_score = df_stats['score'].median()
        hit_rate_high = (df_stats[df_stats['score'] >= median_score]['kill_rate'] > 0).mean()
        hit_rate_low = (df_stats[df_stats['score'] < median_score]['kill_rate'] > 0).mean()
        
        print(f"\nHit Rate (Kill > 0) in High Score Group (>=Median): {hit_rate_high*100:.2f}%")
        print(f"Hit Rate (Kill > 0) in Low Score Group (<Median): {hit_rate_low*100:.2f}%")

        # 3. 斯皮尔曼等级相关系数 (Spearman) - 对非正态分布数据更鲁棒
        spearman_corr, s_p_value = stats.spearmanr(heuristic_scores, mutation_kills)
        print(f"\nSpearman Rank Correlation: {spearman_corr:.4f}")
        print(f"Spearman P-value: {s_p_value:.4e}")
    except Exception as e:
        print(f"\n[WARN] Additional stats analysis failed: {e}")
else:
    print(f"Not enough samples ({len(heuristic_scores)}) for correlation analysis.")
print("="*40)
