import os
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
import sys
import shutil
import tempfile
import traceback

# 导入分析工具 (确保 PYTHONPATH 包含 EasyR1/examples/reward_function)
sys.path.insert(0, "/home/ubuntu/myren/EasyR1/examples/reward_function")
from soot_dynamic_utills import analyze_variable_flow, perform_dynamic_analysis

# 配置路径
CWD = "/home/ubuntu/myren/SF110"
INPUT_JSON = "/home/ubuntu/myren/SF110/qwen_train.json"
OUTPUT_JSON = "/home/ubuntu/myren/SF110/qwen_train_preprocessed.json"
NUM_WORKERS = 8  # 按照 CPU 核心数调整

def process_single_item(item):
    """
    对单个样本进行预处理：Soot 静态分析 + Dynamic 分析。
    为了保持项目纯净，Dynamic 分析在临时目录中进行。
    """
    # 1. Soot 静态分析 (只分析原始项目字节码，由于是只读，无需拷贝项目)
    if 'soot_analysis_result' not in item:
        try:
            # analyze_variable_flow 内部会去 CWD/bug_num_project/target/classes 读
            soot_result = analyze_variable_flow(item)
            item['soot_analysis_result'] = soot_result
        except Exception as e:
            item['soot_analysis_result'] = {'error': f"Soot failed: {str(e)}"}

    # 2. Dynamic 动态分析 (涉及插桩修改，必须使用临时拷贝以保持项目纯净)
    if 'dynamic_analysis' not in item:
        temp_dir = None
        try:
            project = item.get('project', '')
            bug_num = item.get('bug_num', '')
            dir_name = f"{bug_num}_{project}"
            src_project_path = os.path.join(CWD, dir_name)
            
            if not os.path.exists(src_project_path):
                raise FileNotFoundError(f"Project path {src_project_path} not found")

            # 创建独立临时工作空间
            temp_dir = tempfile.mkdtemp(prefix=f"pre_dyn_{dir_name}_")
            project_path = os.path.join(temp_dir, dir_name)
            
            # 使用硬链接拷贝 (极速且省空间)
            import subprocess
            subprocess.run(['cp', '-al', src_project_path, project_path], check=True, stderr=subprocess.DEVNULL)
            
            # 独立拷贝 target 避免冲突
            src_target = os.path.join(src_project_path, 'target')
            dst_target = os.path.join(project_path, 'target')
            if os.path.exists(dst_target):
                shutil.rmtree(dst_target)
                if os.path.exists(src_target):
                    shutil.copytree(src_target, dst_target)

            # 计算相对路径
            main_method_path = item.get('main_method_path', '')
            test_method_path = item.get('test_method_path', '')
            dyn_rel_main = os.path.relpath(main_method_path, src_project_path)
            dyn_rel_test = os.path.relpath(test_method_path, src_project_path)
            
            # 解除硬链接以允许插桩修改
            for rel_p in [dyn_rel_main, dyn_rel_test]:
                p = os.path.join(project_path, rel_p)
                if os.path.exists(p):
                    with open(p, 'r') as f: content = f.read()
                    os.unlink(p)
                    with open(p, 'w') as f: f.write(content)

            # 执行动态分析 (内部会修改 project_path 下的文件并编译运行)
            dyn_result = perform_dynamic_analysis(item, project_path, dyn_rel_main, dyn_rel_test)
            item['dynamic_analysis'] = dyn_result
            
        except Exception as e:
            item['dynamic_analysis'] = {'error': f"Dynamic failed: {str(e)}"}
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                
    return item

def main():
    print(f"Reading {INPUT_JSON}...")
    with open(INPUT_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    total = len(data)
    print(f"Total items to process: {total}")

    processed_data = []
    
    # 使用进程池并行处理
    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        future_to_idx = {executor.submit(process_single_item, item): i for i, item in enumerate(data)}
        
        count = 0
        for future in as_completed(future_to_idx):
            try:
                res = future.result()
                processed_data.append(res)
                count += 1
                if count % 10 == 0:
                    print(f"Progress: {count}/{total} ({(count/total)*100:.2f}%)")
            except Exception as exc:
                print(f"Item generated an exception: {exc}")

    print(f"Saving preprocessed data to {OUTPUT_JSON}...")
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(processed_data, f, ensure_ascii=False, indent=2)
    print("Done!")

if __name__ == "__main__":
    main()
