import os
import json
import subprocess
import shutil
import glob

# 配置路径
CWD = "/home/ubuntu/myren/SF110"
PIT_LIB_DIR = os.path.join(CWD, "pitest_lib")
PIT_JARS = [
    os.path.join(PIT_LIB_DIR, "pitest.jar"),
    os.path.join(PIT_LIB_DIR, "pitest-command-line.jar"),
    os.path.join(PIT_LIB_DIR, "pitest-entry.jar"),
    os.path.join(PIT_LIB_DIR, "commons-text.jar"),
    os.path.join(PIT_LIB_DIR, "commons-lang3.jar")
]
# SF110 公共库目录
SF110_COMMON_LIB = os.path.join(CWD, "lib")

def get_project_classpath(project_path):
    """
    获取项目的完整 classpath，包括：
    1. 项目根目录
    2. 项目内的 lib 和 test-lib
    3. SF110 公共 lib
    4. 常见的编译输出目录 (target/classes, build/classes, bin)
    """
    cp_elements = [project_path]
    
    # 查找所有 jar
    search_dirs = [
        os.path.join(project_path, "lib"),
        os.path.join(project_path, "test-lib"),
        SF110_COMMON_LIB
    ]
    for d in search_dirs:
        if os.path.isdir(d):
            for jar in glob.glob(os.path.join(d, "**/*.jar"), recursive=True):
                cp_elements.append(jar)
    
    # 查找 classes 目录
    classes_dirs = ["target/classes", "build/classes", "bin", "build/classes/main"]
    for cd in classes_dirs:
        full_cd = os.path.join(project_path, cd)
        if os.path.isdir(full_cd):
            cp_elements.append(full_cd)
            
    return ":".join(list(set(cp_elements)))

def run_pitest(project_path, target_class, test_class, target_method, report_dir):
    """
    运行 PITest 命令行，限制为特定的类和方法
    """
    project_cp = get_project_classpath(project_path)
    # 合并 PITest 自身的 jar 和项目的 cp
    full_cp = ":".join(PIT_JARS) + ":" + project_cp
    
    # 确定 sourceDir (SF110 通常是 src/main/java)
    source_dir = os.path.join(project_path, "src/main/java")
    if not os.path.exists(source_dir):
        source_dir = os.path.join(project_path, "src") # 备选
        
    # 确定 mutableCodePaths (通常是编译后的 classes 目录)
    mutable_path = os.path.join(project_path, "target/classes")
    if not os.path.exists(mutable_path):
        mutable_path = os.path.join(project_path, "build/classes")

    if not os.path.exists(mutable_path):
        print(f"[ERROR] No classes found for mutation in {project_path}. Did you compile?")
        return False

    # 构造 PITest 命令
    # 注意：某些版本不一定支持 --includedMethods，改为在 targetClasses 中包含方法过滤（如果支持）
    # 或者使用更通用的过滤方式
    cmd = [
        "java",
        "-Xmx2g",
        "-cp", full_cp,
        "org.pitest.mutationtest.commandline.MutationCoverageReport",
        "--reportDir", report_dir,
        "--targetClasses", target_class, 
        "--targetTests", test_class,
        "--sourceDirs", source_dir,
        "--mutableCodePaths", mutable_path,
        "--outputFormats", "XML,HTML",
        "--timestampedReports=false",
        "--mutators", "STRONGER",
        "--features", f"+CLASSLIMIT(limit[15])"
    ]
    
    # 尝试使用正确的参数名来过滤方法
    # PITest 命令行中通常不直接支持 --includedMethods，
    # 某些版本支持在 targetClasses 后面加通配符或使用特定的 filter
    # 这里的 STRONGER 已经过滤了一部分，我们再通过目标类来收缩范围


    print(f"[INFO] Running PITest for {target_class}.{target_method}...")
    try:
        result = subprocess.run(cmd, cwd=project_path, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"[ERROR] PITest failed with return code {result.returncode}")
            # print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print(f"[ERROR] PITest timed out for {target_class}")
        return False
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        return False

def main(input_json):
    with open(input_json, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for item in data:
        project = item.get('project')
        bug_num = item.get('bug_num')
        # 获取 package.ClassName
        # 这里改进：优先从 test_method_path 或 main_method_path 的包名拼装，
        # 因为 test_name 有时可能不带包名
        target_class = item.get('test_name') 
        target_method = item.get('extracted_method_name') # 获取 focal method 名
        test_method_path = item.get('test_method_path')
        
        # 尝试推断 target_class 的完整路径
        if 'source_file_path' in item: # 如果之前的 augment 存过路径
            pass # 逻辑可以用这个，但最准的是从 test_name 推断
        
        # 简单推断测试类名 (从路径推断)
        test_class = None
        if test_method_path and 'evosuite-tests/' in test_method_path:
            rel_test = test_method_path.split('evosuite-tests/')[-1].replace('.java', '').replace('/', '.')
            test_class = rel_test

        # 确定项目路径
        project_dir_name = f"{bug_num}_{project}"
        project_path = os.path.join(CWD, project_dir_name)
        if not os.path.isdir(project_path):
            project_path = os.path.join(CWD, f"{project}_{bug_num}")
            if not os.path.isdir(project_path):
                print(f"[SKIP] Project directory not found: {project_dir_name}")
                continue

        # 检查是否已编译，如果没有则尝试编译
        if not os.path.exists(os.path.join(project_path, "target/classes")) and \
           not os.path.exists(os.path.join(project_path, "build/classes")):
            print(f"[INFO] Attempting to compile {project_path}...")
            if os.path.exists(os.path.join(project_path, "fast_compile.sh")):
                subprocess.run(["bash", "fast_compile.sh"], cwd=project_path)
            elif os.path.exists(os.path.join(project_path, "compile.sh")):
                subprocess.run(["bash", "compile.sh"], cwd=project_path)

        report_dir = os.path.join(project_path, "pit_reports")
        if os.path.exists(report_dir):
            shutil.rmtree(report_dir)
        os.makedirs(report_dir)

        success = run_pitest(project_path, target_class, test_class, target_method, report_dir)
        
        if success:
            xml_report = os.path.join(report_dir, "mutations.xml")
            if os.path.exists(xml_report):
                print(f"[SUCCESS] PITest finished for {target_class}. Report at {xml_report}")
                # 此处可以增加逻辑解析 XML 并将变异结果存回 item['pit_results']
            else:
                print(f"[WARNING] PITest finished but mutations.xml not found for {target_class}")

if __name__ == "__main__":
    # 示例运行前 5 个 (避免时间太长)
    main("/home/ubuntu/myren/SF110/qwen_test.json")
