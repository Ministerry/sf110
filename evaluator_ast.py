import os
import json
import pandas as pd
import shutil
import re
import subprocess
import tempfile
import json
from utils import *
from examples.reward_function.train_utils import *
import sys
from soot_static_utils import *
from rewards_train_utils import *
# 1. 结合好items和generation 
CWD = "/home/ubuntu/myren/SF110"
path = "/home/ubuntu/myren/SF110"
items_path = "/home/ubuntu/myren/SF110/rl_test_test_filter_end.json"      # 替换为easyR1的数据集path     #替换为模型生成的path

with open(items_path,"r",encoding="utf-8") as f:
    data = json.load(f)

pred_file = os.environ.get("PRED_FILE", "/home/ubuntu/myren/llama/LLaMA-Factory/saves/Qwen2.5-1.5B-Instruct/lora/eval_origin_sft_5_14/generated_predictions.jsonl")
print(f"Using prediction file: {pred_file}")

# generation = []
# with open(pred_file,"r",encoding="utf-8") as f:
#     for line in f:
#         pred = json.loads(line.strip())
#         generation.append(strip_java_guard(pred['predict']))

# gen_count = 0
# for i in range(len(data)):
#     if len(data[i]['ast_generates']) != 0:
#         data[i]['predict'] = generation[gen_count]
#         gen_count += 1

# print(gen_count)

skip_or_not = 0

for i in range(len(data)):
        
    print(f"Processing item {i+1}/{len(data)} (ID: {data[i].get('id')})")
    
    if 'soot_analysis_result' not in data[i]:
        try:
            soot_result = analyze_variable_flow(data[i])
            print(f"[Soot Analysis] Result: {json.dumps(soot_result)}")
            data[i]['soot_analysis_result'] = soot_result
        except Exception as e:
            print(f"[WARN] Soot Analysis Failed: {e}")
            data[i]['soot_analysis_result'] = {'error': str(e)}

    # if 'dynamic_analysis' not in data[i]:
    #     focal_method_code = data[i].get('focal_method', '')
    #     extra_info = data[i]
        
    #     # Load ast_generates early
    #     ast_generates = extra_info.get("ast_generates", [])
        
    #     # Extract basic info needed for path construction
    #     project = extra_info.get('project', '')
    #     bug_num = extra_info.get('bug_num', '')
    #     dir_name_base = f"{bug_num}_{project}"
    #     main_method_path_base = extra_info.get('main_method_path', '')
    #     test_method_path_base = extra_info.get('test_method_path', '')
    #     src_project_path_base = os.path.join(CWD, dir_name_base)
    #     # Only need to check the base case once, not for every variant
    #     try:
    #         print(f"Running Dynamic Analysis for item {i}")
    #         # Need to setup project path temporarily
    #         dyn_temp_dir = tempfile.mkdtemp(prefix=f"dynamic_{bug_num}_{project}_")
    #         dyn_project_path = os.path.join(dyn_temp_dir, dir_name_base)
            
    #         # Copy project
    #         subprocess.run(['cp', '-al', src_project_path_base, dyn_project_path], check=True, stderr=subprocess.DEVNULL)
    #         # Handle target
    #         src_target = os.path.join(src_project_path_base, 'target')
    #         dst_target = os.path.join(dyn_project_path, 'target')
    #         if os.path.exists(dst_target): shutil.rmtree(dst_target)
    #         # Using copytree with dirs_exist_ok=True if python 3.8+ else ignore
    #         if os.path.exists(src_target): shutil.copytree(src_target, dst_target)
            
    #         # Paths
    #         dyn_rel_main = os.path.relpath(main_method_path_base, src_project_path_base)
    #         dyn_rel_test = os.path.relpath(test_method_path_base, src_project_path_base)
            
    #         # Unlink crucial files
    #         dyn_full_test = os.path.join(dyn_project_path, dyn_rel_test)
    #         if os.path.exists(dyn_full_test):
    #             with open(dyn_full_test, 'r') as f: c = f.read()
    #             os.unlink(dyn_full_test)
    #             with open(dyn_full_test, 'w') as f: f.write(c)

    #         dyn_result = perform_dynamic_analysis(data[i], dyn_project_path, dyn_rel_main, dyn_rel_test)
    #         data[i]['dynamic_analysis'] = dyn_result
    #         print(f"[Dynamic Analysis] Result: {json.dumps(dyn_result)}")
    #         shutil.rmtree(dyn_temp_dir, ignore_errors=True)
            
    #     except Exception as e:
    #         print(f"[WARN] Dynamic Analysis Failed: {e}")
    #         data[i]['dynamic_analysis'] = {'error': str(e)}
    #     finally:
    #         if 'dyn_temp_dir' in locals() and os.path.exists(dyn_temp_dir):
    #             shutil.rmtree(dyn_temp_dir, ignore_errors=True)

        
    if skip_or_not == 1:
        continue

    reward = 0
    n = 0
    ast_generates = []
    temp_main_method_path = ""
    project_path = ""
    temp_dir = None
    focal_method_output = None
    backup_file = None

    # 初始化默认评估结果
    data[i]['rewards'] = 0.0
    data[i]['kill_count'] = 0
    data[i]['valid_mutant_count'] = 0
    data[i]['total_mutants'] = 0
    # 初始化编译与断言有效标志
    data[i]['compile_pass'] = False
    data[i]['assertion_valid'] = False

    try:
        assertion = data[i]['assert']
        print(assertion)
        # CFG based degree calculation for Focal Method

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
        prefix = extra_info.get('prefix', '')
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
            data[i]['rewards'] = 0 # 原程序没通过，断言无效，记为 0 杀怪
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
            data[i]['rewards'] = 0
            print("原始代码运行失败/超时")
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            continue
        else:
            out_text = (test_ret.stdout or "") + "\n" + (test_ret.stderr or "")

        # 解析 JUnit 输出
        summary = parse_junit_output(out_text)
        if summary.get("ok") is False:
            print("原始代码运行不通过 (假阳性断言，击杀无效)")
            data[i]['rewards'] = 0
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            continue
        
        focal_method_output = "PASS"
        # 原始代码+断言编译并且运行通过
        data[i]['compile_pass'] = True

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
            
            # 变体编译通过，记为有效变异体
            data[i]['valid_mutant_count'] = data[i].get('valid_mutant_count', 0) + 1
            
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
    
    # 将奖励替换为击杀率 (Kill Rate)
    total_mutants = len(ast_generates)
    kill_rate = n / total_mutants if total_mutants > 0 else 0.0
    
    data[i]['rewards'] = kill_rate
    data[i]['kill_count'] = n
    data[i]['total_mutants'] = total_mutants
    # 断言有效当且仅当至少击杀一个变异体
    data[i]['assertion_valid'] = True if n > 0 else False
    
    print(f"变异体杀死数量 (断言): {n}/{total_mutants}, 击杀率: {kill_rate:.4f}")
    
    
# # --- 汇总指标计算: 变异测试得分、有效断言率、编译通过率 ---
output_name = "excution_origin_evosuite_rl_test_test_filter_end.json"

# 统计项
total_items = len(data)
items_with_predict = sum(1 for item in data if item.get('predict'))
total_kills = sum(item.get('kill_count', 0) for item in data)
total_mutants_all = sum(item.get('total_mutants', 0) for item in data)
total_valid_mutants = sum(item.get('valid_mutant_count', 0) for item in data)

mutation_test_score = (total_kills / total_mutants_all) if total_mutants_all > 0 else 0.0
valid_mutant_rate = (total_valid_mutants / total_mutants_all) if total_mutants_all > 0 else 0.0
effective_assertions = sum(1 for item in data if item.get('assertion_valid'))
effective_assertion_rate = (effective_assertions / items_with_predict) if items_with_predict > 0 else 0.0
compile_passes = sum(1 for item in data if item.get('compile_pass'))
compile_pass_rate = (compile_passes / items_with_predict) if items_with_predict > 0 else 0.0

metrics = {
    'mutation_test_score': mutation_test_score,
    'valid_mutant_rate': valid_mutant_rate,
    'effective_assertion_rate': effective_assertion_rate,
    'compile_pass_rate': compile_pass_rate,
    'total_items': total_items,
    'items_with_predict': items_with_predict,
    'total_kills': total_kills,
    'total_mutants': total_mutants_all,
    'total_valid_mutants': total_valid_mutants
}

print("[METRICS] Mutation test score (overall kill rate): {:.4f}".format(mutation_test_score))
print("[METRICS] Valid mutant rate: {:.4f} ({}/{})".format(valid_mutant_rate, total_valid_mutants, total_mutants_all))
print("[METRICS] Effective assertion rate: {:.4f} ({}/{})".format(effective_assertion_rate, effective_assertions, items_with_predict))
print("[METRICS] Compile pass rate: {:.4f} ({}/{})".format(compile_pass_rate, compile_passes, items_with_predict))

# Save metrics beside the output JSON
metrics_name = output_name.replace('.json', '_metrics.json')
with open(metrics_name, 'w', encoding='utf-8') as mf:
    json.dump(metrics, mf, ensure_ascii=False, indent=2)
print(f"[INFO] Wrote metrics to {metrics_name}")

with open(output_name, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"[INFO] Wrote {len(data)} reprocessed records to {output_name}")
