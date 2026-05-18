import os
import json
import pandas as pd
import shutil
import re
import subprocess
import tempfile
import sys
from utils import *
from examples.reward_function.train_utils import *
from soot_dynamic_utills import *

CWD = "/home/ubuntu/myren/SF110"

def main():
    # 1. 加载包含 PIT 变体的 JSON 文件
    items_path = os.path.join(CWD, "qwen_test_with_pit.json")
    if not os.path.exists(items_path):
        print(f"File not found: {items_path}")
        return

    with open(items_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # items_path = os.path.join(CWD, "test_deepseek.json")    
    # with open(items_path, "r", encoding="utf-8") as f:
    #     deepseek = json.load(f)
    # 2. 获取模型预测文件 (如果是评估模型生成的断言)
    pred_file = os.environ.get("PRED_FILE", "qwen2.5_1.5b_4_5_after_generated_predictions.jsonl")
    generation = []
    if os.path.exists(pred_file):
        print(f"Using prediction file: {pred_file}")
        with open(pred_file, "r", encoding="utf-8") as f:
            for line in f:
                pred = json.loads(line.strip())
                generation.append(strip_java_guard(pred['predict']))
        
        # 将预测映射到数据中 (仅针对有变体的数据)
        gen_count = 0
        for i in range(len(data)):
            if len(data[i].get('pit_generates', [])) != 0:
                if gen_count < len(generation):
                    data[i]['predict'] = generation[gen_count]
                    gen_count += 1
    else:
        print(f"Prediction file {pred_file} not found, using default 'assert' field for evaluation.")
        for i in range(len(data)):
            data[i]['predict'] = data[i].get('assert', '')

    # 3. 开始评估
    for i in range(len(data)):
        item = data[i]
        pit_mutants = item.get('pit_generates', [])
        if not pit_mutants:
            continue

        print(f"[{i+1}/{len(data)}] Evaluation for {item.get('project')} - {item.get('extracted_class_name')}.{item.get('extracted_method_name')}")
        
        n_killed = 0
        n_total = len(pit_mutants)
        
        project = item.get('project', '')
        bug_num = item.get('bug_num', '')
        src_project_path = os.path.join(CWD, f"{bug_num}_{project}")
        
        temp_dir = tempfile.mkdtemp(prefix=f"pit_eval_{bug_num}_{project}_")
        project_path = os.path.join(temp_dir, f"{bug_num}_{project}")

        try:
            # 1. 优化：使用 hardlink 复制全量项目，确保 lib/ 等依赖存在
            subprocess.run(['cp', '-al', src_project_path, project_path], check=True, stderr=subprocess.DEVNULL)

            # 2. 处理 target 目录，避免共享编译产物
            src_target = os.path.join(src_project_path, 'target')
            dst_target = os.path.join(project_path, 'target')
            
            if os.path.exists(dst_target):
                shutil.rmtree(dst_target, ignore_errors=True)
            
            if os.path.exists(src_target):
                shutil.copytree(src_target, dst_target, dirs_exist_ok=True)

            main_method_path = item.get('main_method_path', '')
            test_method_path = item.get('test_method_path', '')
            rel_main = os.path.relpath(main_method_path, src_project_path)
            rel_test = os.path.relpath(test_method_path, src_project_path)
            
            temp_main_path = os.path.join(project_path, rel_main)
            temp_test_path = os.path.join(project_path, rel_test)

            # 解除文件链接
            for p in [temp_main_path, temp_test_path]:
                if os.path.exists(p):
                    with open(p, 'r') as f: content = f.read()
                    os.unlink(p)
                    with open(p, 'w') as f: f.write(content)

            # 插入模型断言
            assertion = item.get('predict', '')
            print(assertion)
            prefix = item.get('prefix', '')
            test_code = "public void test_pit_eval() throws Throwable { \n " + prefix + "\n" + assertion + "\n}"
            replace_from_first_brace(temp_test_path, test_code, f"{bug_num}_{project}")

            # 初始检查：原程序+新断言必须通过 (否则是假阳性)
            compile_ret = optimized_compile(project_path, [rel_main, rel_test])
            
            # 如果极速编译失败 (通常因为 Classpath 没对齐)，回退到 fast_compile.sh
            if compile_ret is None or compile_ret.returncode != 0:
                compile_ret = run_cmd_with_timeout(
                    ['bash', 'fast_compile.sh'],
                    cwd=project_path,
                    timeout=COMPILE_TIMEOUT
                )

            if compile_ret is None or compile_ret.returncode != 0:
                print("  [FAIL] Initial compilation failed")
                continue

            test_class = item.get('test_name', '') + 'EvoSuiteTest'
            run_ret = run_cmd_with_timeout(['bash', 'run.sh', test_class], cwd=project_path)
            if run_ret is None or "OK" not in (run_ret.stdout or ""):
                print("  [FAIL] Test with new assertion failed on original code")
                continue

            # 遍历 PIT 产生的变体
            for mutant in pit_mutants:
                mutated_code = mutant.get('mutated_code_line_guess')
                line_idx = mutant.get('line') 
                mutated_class = mutant.get('mutated_class')
                
                # 注意：PIT 可能变异非焦点类。这里我们假设变异发生在 temp_main_path 
                # 如果要支持全类变异，需要根据 mutated_class 动态定位文件，
                # 但目前我们主要关注 Focal Method
                
                # 简单实现：仅替换指定行进行测试
                with open(temp_main_path, 'r') as f:
                    lines = f.readlines()
                
                if line_idx is not None and int(line_idx) <= len(lines):
                    original_line = lines[int(line_idx)-1]
                    lines[int(line_idx)-1] = mutated_code + "\n"
                    with open(temp_main_path, 'w') as f:
                        f.writelines(lines)
                    
                    # 编译变体
                    mutant_compile = optimized_compile(project_path, [rel_main])
                    if mutant_compile is None or mutant_compile.returncode != 0:
                        mutant_compile = run_cmd_with_timeout(
                            ['bash', 'fast_compile.sh'],
                            cwd=project_path,
                            timeout=COMPILE_TIMEOUT
                        )

                    if mutant_compile and mutant_compile.returncode == 0:
                        mutant_run = run_cmd_with_timeout(['bash', 'run.sh', test_class], cwd=project_path)
                        # 如果运行失败且是 AssertionError，则视为 Kill
                        if mutant_run is None or "java.lang.AssertionError" in (mutant_run.stdout or "") or "Failures: 1" in (mutant_run.stdout or ""):
                            n_killed += 1
                    
                    # 恢复原行
                    lines[int(line_idx)-1] = original_line
                    with open(temp_main_path, 'w') as f:
                        f.writelines(lines)

            kill_rate = n_killed / n_total if n_total > 0 else 0
            item['pit_kill_count'] = n_killed
            item['pit_total_mutants'] = n_total
            item['pit_kill_rate'] = kill_rate
            print(f"  [RESULT] Killed: {n_killed}/{n_total} ({kill_rate:.2%})")

        except Exception as e:
            print(f"  [ERROR] {e}")
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    # 4. 保存结果
    output_path = os.path.join(CWD, "excution_qwen2.5_1.5b_after_pit_evaluation_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved results to {output_path}")

if __name__ == "__main__":
    main()
