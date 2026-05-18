from typing import List
import os
from itertools import zip_longest
import re
from typing import Dict, Any
import subprocess

reward_assignment = {
    "kill_mutation_crash" : 1.0,  # 只是导致程序崩溃，含金量低
    "kill_mutation_logic" : 10.0,  # 精确捕捉到逻辑差异，含金量极高
    "manslaughter" : -5,
    "varies" : 1,
    "have_connection" : 2,
    "no_connection" : -2,
    "no_meaning" : -8,
    "strong_assertion": 3,
    "survivor": -2.0,
    "generic_not_null": -5.0, # 防止模型只写 assertNotNull
}
COMPILE_TIMEOUT = 60 
RUN_TIMEOUT = 20

def _is_strong_assertion(assert_str: str) -> bool:
    """
    判断是否为强效断言 (鼓励使用 assertEquals 等精确检查)
    """
    if not assert_str: return False
    strong_patterns = [
        r'assertEquals\s*\(',
        r'assertArrayEquals\s*\(',
        r'assertNotEquals\s*\(',
        r'assertSame\s*\(',
        r'assertNotSame\s*\(',
        r'assertThrows\s*\(',
        # assertTrue 包含比较运算符，也被视为较强
        r'assertTrue\s*\(.*(?:==|!=|>|<|instanceof).*\)',
        r'assertFalse\s*\(.*(?:==|!=|>|<|instanceof).*\)'
    ]
    # 排除掉被 is_trivial 捕获的简单 != null
    # 如果它既匹配 trivial 又匹配 strong (比如 assertTrue(x != null))，
    # 我们希望它被 trivial 惩罚，而不是获得 strong 奖励。
    # 所以可以在 normalization 里控制，或者在这里如果是 trivial 就返回 False。
    if _is_trivial(assert_str):
        return False
        
    return any(re.search(p, assert_str) for p in strong_patterns)

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

def reward_function(focal_method_output='FAIL', variant_output='FAIL', failure_type=None):
    """
    计算单个变体的原始得分 (Step Reward)。
    failure_type: 'assertion' (逻辑错误) 或 'exception' (崩溃/异常) 或 None
    """
    if focal_method_output == "PASS":
        if variant_output == "FAIL":
            # 区分逻辑捕获和崩溃
            if failure_type == "assertion":
                return reward_assignment["kill_mutation_logic"]
            else:
                 return reward_assignment["kill_mutation_crash"]
        if variant_output == "PASS":
            return reward_assignment["survivor"]
    return 0.0

def normalization(reward, n, focal_method, test_prefix, assertion, num):
    """
    将动态变体得分与静态断言质量得分融合，并进行鲁棒归一化。
    """
    # 如果没有杀死任何变异体，强制进行严厉的惩罚
    if num == 0: 
        if _is_trivial(assertion) or "assertNotNull" in (assertion or ""):
             return -0.8
        else:
             return -0.4

    static_score = 0.0

    static_score += reward_assignment['varies'] * num
    if _has_connection(assertion, focal_method, test_prefix):
        static_score += reward_assignment['have_connection']
    else:
        static_score += reward_assignment['no_connection']

    if _is_strong_assertion(assertion):
        static_score += reward_assignment['strong_assertion']
    
    # [New Strategy] 针对 assertNotNull 的额外惩罚
    if assertion and "assertNotNull" in assertion and not _is_trivial(assertion):
         static_score += reward_assignment['generic_not_null']

    if _is_trivial(assertion or ""):
        static_score += reward_assignment['no_meaning']

    total_raw = reward + static_score

    # 动态上下界：避免强断言奖励被硬编码边界截断
    # R_max 假设全都是高质量逻辑变异击杀
    R_max = (n * reward_assignment['kill_mutation_logic']) + \
        (n * reward_assignment['varies']) + \
        reward_assignment['have_connection'] + \
        reward_assignment['strong_assertion']

    # R_min假设全都是幸存 + 毫无意义 + 惩罚
    R_min = (n * reward_assignment['survivor']) + \
        reward_assignment['no_connection'] + \
        reward_assignment['no_meaning'] + \
        reward_assignment['generic_not_null']

    if R_max <= R_min:
        return 0.0

    total_raw = max(R_min, min(R_max, total_raw))
    r_norm = (total_raw - R_min) / (R_max - R_min)
    return 2 * r_norm - 1
def optimized_compile(cwd, source_files, timeout=COMPILE_TIMEOUT):
    """
    直接调用 javac 编译指定文件列表，无需扫描整个项目。
    如果失败，返回 None (超时/异常)，或 CompletedProcess (含返回码)。
    """
    import signal
    try:
        # 1. 基础环境配置
        JAVA_HOME = os.getenv('JAVA_HOME', '/home/fdse/anaconda3/envs/rmy_llama/lib/jvm')
        JAVAC = os.path.join(JAVA_HOME, 'bin', 'javac')
        
        # 2. 构造 Classpath
        # 包含了 compile.sh 中的所有关键路径
        LIB_BASE = "/home/fdse/rmy/SF110/lib"
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