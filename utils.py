import re
from inject import BugInject
import javalang
from javalang.tree import MethodDeclaration, TryStatement, CatchClause, MethodInvocation
from typing import List, Dict, Any, Optional, Set, Tuple
import os
import html
import pandas as pd
import subprocess
import time
from filelock import FileLock
import traceback
from pathlib import Path
import shutil
from bs4 import BeautifulSoup

java_template = """
public class Test {{
    {code}
}}
"""
CMD_TIMEOUT = 60  # 外部命令超时时间（秒）

def find_matching_brace_smart(text: str, start_pos: int) -> Optional[int]:
    """
    智能的括号匹配器，会跳过注释和字符串中的大括号。
    返回匹配的右大括号 '}' 后面的索引位置 (即替换应该结束的位置)。
    """
    brace_count = 1
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False
    
    i = start_pos
    length = len(text)
    
    while i < length:
        char = text[i]
        
        # 1. 检查是否在注释或字符串中，如果在则只检查退出条件
        if in_line_comment:
            if char == '\n':
                in_line_comment = False
            i += 1
            continue
        
        if in_block_comment:
            if char == '*' and i + 1 < length and text[i+1] == '/':
                in_block_comment = False
                i += 2 # 跳过 '*/'
                continue
            i += 1
            continue
            
        if in_string:
            if char == '\\': # 处理转义字符
                i += 2
                continue
            if char == '"':
                in_string = False
            i += 1
            continue

        if in_char:
            if char == '\\': # 处理转义字符
                i += 2
                continue
            if char == '\'':
                in_char = False
            i += 1
            continue
            
        # 2. 检查是否进入注释或字符串
        if char == '"':
            in_string = True
        elif char == '\'':
            in_char = True
        elif char == '/' and i + 1 < length:
            if text[i+1] == '/':
                in_line_comment = True
                i += 1 # 让它在下一个循环中跳过第二个 '/'
            elif text[i+1] == '*':
                in_block_comment = True
                i += 1 # 让它在下一个循环中跳过 '*'
                
        # 3. 计数
        elif char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                return i + 1  # 返回右大括号后面的位置
        
        i += 1
        
    return None # 未找到匹配的括号
def is_method_worth_injecting(focal_method: str, min_lines: int = 5) -> bool:
    """
    判断方法是否值得注入 bug
    
    Args:
        focal_method: 方法源代码
        min_lines: 最小有效代码行数（默认 5 行）
    
    Returns:
        bool: True 表示值得注入，False 表示跳过
    """
    # 移除空行和注释
    lines = focal_method.split('\n')
    effective_lines = []
    
    for line in lines:
        stripped = line.strip()
        # 跳过空行
        if not stripped:
            continue
        # 跳过注释行
        if stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*'):
            continue
        # 跳过单独的大括号
        if stripped in ['{', '}']:
            continue
        effective_lines.append(stripped)
    
    return len(effective_lines) >= min_lines
def is_path_based_variant_ast(original_code: str, variant_code: str, path_info: List[Any]) -> bool:
    """
    严格版本：仅当 original_code 相对于 variant_code 的 removed 行中有任意一行
    在执行路径 path_info（规范化后）中出现时才返回 True。
    """
    def norm_line(line: str) -> str:
        if not line:
            return ''
        s = line.strip()
        # 0. Strip comments to handle javalang unparse dropping them
        s = re.sub(r'//.*$', '', s)
        s = re.sub(r'/\*.*?\*/', '', s, flags=re.DOTALL)
        
        # 1. basic cleanup of block markers: remove ALL braces to handle line splitting/joining robustly
        s = s.replace('{', ' ').replace('}', ' ')
        s = s.strip().rstrip(';').strip()
        
        # 1.5. Normalize punctuation spacing: add spaces around logical/math operators and commas
        # This handles 'a=b' vs 'a = b' and 'f(a,b)' vs 'f(a, b)' caused by unparser differences
        s = re.sub(r'([,.+\-*/%=<>!&|^])', r' \1 ', s)

        # 2. normalize whitespace and parens spacing to standard form first
        s = re.sub(r'\s+', ' ', s).strip().lower()
        # Collapse multiple parens spaces: "(  (" -> "((", ")  )" -> "))"
        s = re.sub(r'\(\s+', '(', s)
        s = re.sub(r'\s+\)', ')', s)
        
        # 3. Recursively remove outer parentheses: ((exp)) -> exp
        # loop until stable to handle multiple layers
        while True:
            # Special check: prevent stripping (a) + (b) -> a) + (b which is invalid
            # Only strip if it matches pairs (this is a heuristic, counting balance is better but expensive)
            if s.startswith('(') and s.endswith(')'):
                # Simple check: if we strip, is the inside balanced?
                inner = s[1:-1].strip()
                # quick balance check on inner
                balance = 0
                possible = True
                for char in inner:
                    if char == '(': balance += 1
                    elif char == ')':
                        balance -= 1
                        if balance < 0: # broken structure like "a) + (b"
                            possible = False
                            break
                if possible and balance == 0:
                    s = inner
                    continue
            break

        # 4. Handle control flow keywords
        # remove common leading type declarations (e.g. "boolean x = ...")
        s = re.sub(r'^(?:byte|short|int|long|float|double|boolean|char|string|[A-Z][A-Za-z0-9_$.<>]*)\s+', '', s, flags=re.I)
        
        # normalize if-condition with return statement
        m = re.match(r'^(?:else\s+if|if)\s*\((.*)\)\s*return\s+(.+?);?$', s, flags=re.I)
        if m:
            cond = m.group(1).strip()
            ret = m.group(2).strip()
            # recurse normalization on parts would be ideal, but here simple strip
            return norm_line(cond) + " return " + norm_line(ret)
            
        # normalize if-condition
        m = re.match(r'^(?:else\s+if|if)\s*\((.*)\)\s*$', s, flags=re.I)
        if m:
            # Just extract condition and continue normalization
            s = m.group(1).strip()
            # Restart normalization for the extracted condition (to strip outer parens of condition)
            return norm_line(s)

        # 5. Final fallback: remove all parentheses to allow "fuzzy" matching
        # This handles javalang's (exp) + (exp) vs exp + exp differences
        s = s.replace('(', ' ').replace(')', ' ')
        s = re.sub(r'\s+', ' ', s).strip()

        return s

    orig_lines = [norm_line(l) for l in original_code.splitlines() if l.strip()]
    var_lines = [norm_line(l) for l in variant_code.splitlines() if l.strip()]

    removed = set(orig_lines) - set(var_lines)
    added = set(var_lines) - set(orig_lines)

    # Robustness Fix: Filter out changes that are merely due to reformatting (line splitting/joining)
    # Check if a "removed" line actually exists in the full variant text
    var_full_text = " " + " ".join(var_lines) + " " # Padding for safety
    real_removed = set()
    for r in removed:
        if not r: continue
        # Robust check: allow flexible spacing matching for checking existence
        # because "join" adds single space, but original might have had none or different.
        # So we normalize spaces in both for this containment check.
        r_ns = r.replace(" ", "")
        var_full_ns = var_full_text.replace(" ", "")
        
        if r_ns not in var_full_ns:
            real_removed.add(r)
    removed = real_removed

    # Check if an "added" line actually exists in the full original text
    orig_full_text = " " + " ".join(orig_lines) + " "
    real_added = set()
    for a in added:
        if not a: continue
        # Robust check: allow flexible spacing matching for checking existence
        a_ns = a.replace(" ", "")
        orig_full_ns = orig_full_text.replace(" ", "")
        if a_ns not in orig_full_ns:
            real_added.add(a)
    added = real_added

    
    # build normalized path element list (flatten path_info)
    path_elems = []
    for p in path_info:
        if isinstance(p, dict) and 'path' in p:
            seq = p['path']
        elif isinstance(p, (list, tuple)):
            seq = p
        else:
            seq = [p]
        for elem in seq:
            if not isinstance(elem, str):
                continue
            ne = norm_line(elem)
            if ne:
                path_elems.append(ne)

    if not path_elems:
        return False

    path_set = set(path_elems)
    
    # print(f"DEBUG: path_set={path_set}")

    # 检查执行路径中的元素是否与变异的代码有某种关联
    # 如果执行路径中的元素在原始代码中存在，但在变异后被修改了，则认为匹配
    for path_elem in path_set:
        if path_elem in removed:
            return True
        
        for r in removed:
            if len(r) < 4: continue
            if r in path_elem or path_elem in r:
                return True

    return False
def is_similar_condition(cond1: str, cond2: str) -> bool:
    """
    检查两个条件语句是否相似（可能只是运算符不同）
    """
    # 移除运算符并比较操作数
    import re
    # 匹配条件语句的基本结构
    pattern = r'(.+?)(\|\||&&|==|!=|<|>|<=|>=)(.+?)(\|\||&&|==|!=|<|>|<=|>=)?(.*)?'
    match1 = re.match(pattern, cond1.replace(' ', ''))
    match2 = re.match(pattern, cond2.replace(' ', ''))
    
    if match1 and match2:
        # 比较操作数是否相似（忽略运算符）
        ops1 = [match1.group(1), match1.group(3), match1.group(5)] if match1.group(5) else [match1.group(1), match1.group(3)]
        ops2 = [match2.group(1), match2.group(3), match2.group(5)] if match2.group(5) else [match2.group(1), match2.group(3)]
        
        # 移除空值并排序以比较操作数
        ops1 = [op.strip() for op in ops1 if op and op.strip()]
        ops2 = [op.strip() for op in ops2 if op and op.strip()]
        
        return sorted(ops1) == sorted(ops2)
    
    # 如果正则匹配失败，直接比较是否包含相同的关键元素
    # 例如比较是否包含相同的变量名
    cond1_lower = cond1.lower()
    cond2_lower = cond2.lower()
    
    # 检查是否包含相同的标识符
    import re
    identifiers1 = set(re.findall(r'\b\w+\b', cond1_lower))
    identifiers2 = set(re.findall(r'\b\w+\b', cond2_lower))
    
    # 检查是否有共同的标识符（除了常见的关键字）
    common_identifiers = identifiers1.intersection(identifiers2) - {'if', 'return', 'null', 'true', 'false', 'int', 'double', 'float', 'boolean', 'string', 'void', 'public', 'private', 'protected', 'static', 'final'}
    
    return len(common_identifiers) >= 2  # 至少有2个共同的标识符
def normalize_code(code: str) -> str:
    """
    标准化Java代码：移除所有注释和空白字符。
    """
    # 1. 移除多行注释 /* ... */
    # 使用 DOTALL 标志让 '.' 也能匹配换行符
    code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
    # 2. 移除单行注释 // ...
    code = re.sub(r'//.*?$', '', code, flags=re.MULTILINE)
    # 3. 移除所有空白字符（空格、制表符、换行符等）
    code = re.sub(r'\s+', '', code)
    return code 
def extract_prefix_and_asserts(method_src: str, test_src: str) -> list:
    """
    给定一个方法源码(method_src)和一个测试方法源码(test_src)，
    返回列表，每项 {'prefix': str, 'assert': str}：
      - 若检测到 try{ ... fail(...)}，返回 prefix 为 try 之前的语句 + try 中 fail 之前的语句，assert 为 'exception'。
      - 否则，对测试中每次调用目标方法的地方，返回该调用及其关联的前置语句（如对象声明），以及与之相关的第一个断言（若能找到）。
    变更：
      - 当为某次调用找到了对应的 assert（非 exception 情况），将返回的 prefix 设为该 assert 之前的所有语句（并从中移除任何其它 assert 语句），保留原有对 try/fail 等特殊结构的处理。
    """
    import re

    def find_matching_brace(text, open_pos):
        L = len(text)
        i = open_pos
        depth = 0
        in_s = in_c = in_line_comment = in_block_comment = False
        while i < L:
            ch = text[i]
            if in_line_comment:
                if ch == '\n':
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                if ch == '*' and i + 1 < L and text[i+1] == '/':
                    in_block_comment = False
                    i += 2
                    continue
            if in_s:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '"':
                    in_s = False
                i += 1
                continue
            if in_c:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '\'':
                    in_c = False
                i += 1
                continue
            if ch == '/' and i + 1 < L:
                if text[i+1] == '/':
                    in_line_comment = True
                    i += 2
                    continue
                if text[i+1] == '*':
                    in_block_comment = True
                    i += 2
                    continue
            if ch == '"':
                in_s = True
                i += 1
                continue
            if ch == '\'':
                in_c = True
                i += 1
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return None

    def find_matching_paren(text, open_pos):
        L = len(text)
        i = open_pos
        depth = 0
        in_s = in_c = in_line_comment = in_block_comment = False
        while i < L:
            ch = text[i]
            if in_line_comment:
                if ch == '\n':
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                if ch == '*' and i + 1 < L and text[i+1] == '/':
                    in_block_comment = False
                    i += 2
                    continue
            if in_s:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '"':
                    in_s = False
                i += 1
                continue
            if in_c:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '\'':
                    in_c = False
                i += 1
                continue
            if ch == '/' and i + 1 < L:
                if text[i+1] == '/':
                    in_line_comment = True
                    i += 2
                    continue
                if text[i+1] == '*':
                    in_block_comment = True
                    i += 2
                    continue
            if ch == '"':
                in_s = True
                i += 1
                continue
            if ch == '\'':
                in_c = True
                i += 1
                continue
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return None

    def count_args_inside(text, open_pos):
        close = find_matching_paren(text, open_pos)
        if close is None:
            return 0
        inside = text[open_pos+1:close].strip()
        if inside == '':
            return 0
        depth = 0
        in_s = in_c = in_line_comment = in_block_comment = False
        cnt = 1
        i = 0
        L = len(inside)
        while i < L:
            ch = inside[i]
            if in_line_comment:
                if ch == '\n':
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                if ch == '*' and i + 1 < L and inside[i+1] == '/':
                    in_block_comment = False
                    i += 2
                    continue
            if in_s:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '"':
                    in_s = False
                i += 1
                continue
            if in_c:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '\'':
                    in_c = False
                i += 1
                continue
            if ch == '/' and i + 1 < L:
                if inside[i+1] == '/':
                    in_line_comment = True
                    i += 2
                    continue
                if inside[i+1] == '*':
                    in_block_comment = True
                    i += 2
                    continue
            if ch == '(':
                depth += 1
            elif ch == ')':
                if depth > 0:
                    depth -= 1
            elif ch == ',' and depth == 0:
                cnt += 1
            i += 1
        return cnt

    def extract_body(s):
        m = re.search(r'\{', s)
        if not m:
            return s
        open_idx = m.start()
        close_idx = find_matching_brace(s, open_idx)
        if close_idx is None:
            return s[open_idx+1:]
        return s[open_idx+1:close_idx]

    # 获取方法名与参数数量
    mname = None
    m = re.search(r'\b(?:public|protected|private)?\s*[\w\<\>\[\]]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', method_src)
    if m:
        mname = m.group(1)
    else:
        m2 = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(', method_src)
        mname = m2.group(1) if m2 else None

    # 尽量使用 javalang 解析方法签名以获取准确的参数数量；解析失败时回退到字符串计数
    m_param_count = 0
    if mname:
        try:
            decl = javalang.parse.parse_member_declaration(method_src)
            if isinstance(decl, MethodDeclaration):
                m_param_count = len(decl.parameters) if decl.parameters is not None else 0
            else:
                sig_pos = method_src.find(mname)
                paren_pos = method_src.find('(', sig_pos) if sig_pos != -1 else -1
                if paren_pos != -1:
                    try:
                        m_param_count = count_args_inside(method_src, paren_pos)
                    except Exception:
                        m_param_count = 0
        except Exception:
            sig_pos = method_src.find(mname)
            paren_pos = method_src.find('(', sig_pos) if sig_pos != -1 else -1
            if paren_pos != -1:
                try:
                    m_param_count = count_args_inside(method_src, paren_pos)
                except Exception:
                    m_param_count = 0

    test_body = extract_body(test_src)

    # try { ... fail(...) } 情形（不受参数约束）
    for tp in re.finditer(r'\btry\s*\{', test_body):
        tp_idx = tp.start()
        brace_pos = test_body.find('{', tp_idx)
        if brace_pos == -1:
            continue
        end_brace = find_matching_brace(test_body, brace_pos)
        if end_brace is None:
            continue
        try_block = test_body[brace_pos+1:end_brace]
        if 'fail(' in try_block:
            fail_rel = try_block.find('fail(')
            inner_before_fail = try_block[:fail_rel]

            # 更稳健：按语法状态切分以分号结束的语句（尊重字符串/注释），并选出在 try 之前结束的语句
            def split_statements_with_spans(text):
                stmts = []
                L = len(text)
                i = 0
                start = 0
                in_s = in_c = in_line_comment = in_block_comment = False
                while i < L:
                    ch = text[i]
                    if in_line_comment:
                        if ch == '\n':
                            in_line_comment = False
                        i += 1
                        continue
                    if in_block_comment:
                        if ch == '*' and i+1 < L and text[i+1] == '/':
                            in_block_comment = False
                            i += 2
                            continue
                        i += 1
                        continue
                    if in_s:
                        if ch == '\\':
                            i += 2
                            continue
                        if ch == '"':
                            in_s = False
                        i += 1
                        continue
                    if in_c:
                        if ch == '\\':
                            i += 2
                            continue
                        if ch == "'":
                            in_c = False
                        i += 1
                        continue
                    if ch == '/' and i+1 < L:
                        if text[i+1] == '/':
                            in_line_comment = True
                            i += 2
                            continue
                        if text[i+1] == '*':
                            in_block_comment = True
                            i += 2
                            continue
                    if ch == '"':
                        in_s = True
                        i += 1
                        continue
                    if ch == "'":
                        in_c = True
                        i += 1
                        continue
                    if ch == ';':
                        end = i+1
                        stmt = text[start:end]
                        stmts.append((stmt, start, end))
                        start = end
                        i += 1
                        continue
                    i += 1
                # trailing fragment (no semicolon) ignored
                return stmts

            all_stmts_with_spans = split_statements_with_spans(test_body)
           # 基于 span 收集所有在 try 之前结束的语句（去除 assert）
            candidates = []
            for s, s_start, s_end in all_stmts_with_spans:
                if s_end <= brace_pos:
                    candidates.append((s_start, s_end, s.strip()))

            # 有时分割器可能漏掉带数组下标或复杂表达式的赋值（如 a[0] = "...";）
            # 额外扫描 try 之前的片段，补充所有包含赋值的语句（排除断言）
            pre_fragment = test_body[:brace_pos]
            pre_stmts_fragment = split_statements_with_spans(pre_fragment)
            existing_texts = {txt for (_s, _e, txt) in candidates}
            for s, s_start, s_end in pre_stmts_fragment:
                txt = s.strip()
                if not txt:
                    continue
                if txt in existing_texts:
                    continue
                # include assignment-like statements that are not asserts
                if '=' in txt and not ('assert' in txt or 'Assert.' in txt):
                    candidates.append((s_start, s_end, txt))

            # 额外使用更宽松的正则补抓（保险起见），但只在 try 之前添加
            for m in re.finditer(r'[^\n;]*\[[^\]]+\]\s*=\s*[^;]+;', pre_fragment):
                txt = m.group(0).strip()
                if txt not in existing_texts:
                    candidates.append((m.start(), m.end(), txt))

            # 按位置排序并去重，去掉任何 assert 语句，标准化为以分号结尾的形式
            candidates = sorted(candidates, key=lambda x: x[1])
            pre_stmts = []
            seen = set()
            for _s, _e, txt in candidates:
                s_strip = txt.rstrip(';').strip() + ';'
                if not s_strip or ('assert' in s_strip or 'Assert.' in s_strip):
                    continue
                if s_strip in seen:
                    continue
                seen.add(s_strip)
                pre_stmts.append(s_strip)

            # collect statements inside try before fail (现有逻辑）
            inner_matches = [s.strip() for s in re.findall(r'[^;]+;', inner_before_fail, flags=re.DOTALL) if s.strip()]
            inner_stmts = [s.rstrip(';').strip() + ';' for s in inner_matches]

            parts = pre_stmts + inner_stmts
            prefix = ' '.join(parts).replace(';;', ';').strip()
            prefix = re.sub(r'^\s*try\s*\{', '', prefix)
            prefix = re.sub(r'\}\s*$', '', prefix).strip()
            return [{'prefix': prefix.strip(), 'assert': 'exception'}]

    stmts = re.findall(r'[^;]+;', test_body, flags=re.DOTALL)
    stmts = [s.strip() for s in stmts if s.strip()]

    results = []
    used_asserts = set()
    if not mname:
        return [{'prefix': test_body.strip(), 'assert': ''}]

    for i, stmt in enumerate(stmts):
        # 查找方法名出现的位置（可能有多个），对每个出现检查括号内参数数量是否匹配方法签名
        for mcall in re.finditer(r'\b' + re.escape(mname) + r'\s*\(', stmt):
            open_paren_idx = stmt.find('(', mcall.start())
            # 解析调用处参数数量（出错时设置为 -1 表示未知/解析失败）
            call_arg_count = -1
            if open_paren_idx != -1:
                try:
                    call_arg_count = count_args_inside(stmt, open_paren_idx)
                except Exception:
                    call_arg_count = -1
            if call_arg_count >= 0 and call_arg_count != m_param_count:
                continue
            if m_param_count > 0:
                if call_arg_count <= 0:
                    continue
                if call_arg_count != m_param_count:
                    continue
            if ('assert' in stmt or 'Assert.' in stmt) and not re.search(r'=\s*' + re.escape(mname) + r'\s*\(', stmt):
                # 如果这个断言已经在 earlier related_asserts 中被关联过，跳过以避免重复
                if i in used_asserts:
                   break
                assertion_stmt = stmt
                # 提取 assert 中可能的标识符（变量名）
                ids = re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\b', assertion_stmt)
                # 移除 assert/Assert 等关键词
                ids = [x for x in ids if x not in ('assert', 'Assert', 'assertEquals', 'assertNotNull', 'assertTrue', 'assertFalse', 'fail')]
                # 检查这些 id 是否在之前的语句中出现过
                seen_before = set()
                for k in range(0, i):
                    seen_before.update(re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\b', stmts[k]))
                # 如果 assert 中至少有一个标识符在之前出现过，则认为该 assert 有效
                if any(s in seen_before for s in ids):
                    prefix_parts = []
                    j = i - 1
                    while j >= 0:
                        s = stmts[j]
                        if re.match(r'\btry\b', s) or re.match(r'\bcatch\b', s) or 'fail(' in s:
                            break
                        if ('assert' in s or 'Assert.' in s):
                            j -= 1
                            continue
                        prefix_parts.append(s)
                        j -= 1
                    prefix_parts.reverse()
                    prefix = ' '.join(prefix_parts).strip()
                    results.append({'prefix': prefix, 'assert': assertion_stmt.strip()})
                    used_asserts.add(i)
                    break
                # 否则跳过这个早期的 assert（可能是误放置的断言），继续处理后续语句
                continue
            call_stmt = stmt
            assigned_var = None
            massign = re.match(r'^[^=;]*\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*', call_stmt)
            if massign:
                assigned_var = massign.group(1)
            mrecv = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*' + re.escape(mname) + r'\s*\(', call_stmt)
            recv = mrecv.group(1) if mrecv else None

            prefix_parts = []
            if recv:
                k = i - 1
                while k >= 0:
                    s = stmts[k]
                    if re.match(r'\btry\b', s) or re.match(r'\bcatch\b', s) or 'fail(' in s:
                        break
                    if ('assert' in s or 'Assert.' in s):
                        k -= 1
                        continue
                    if re.search(r'\b' + re.escape(recv) + r'\b', s) or re.search(r'\bnew\b', s):
                        prefix_parts.insert(0, s)
                        k -= 1
                        continue
                    k -= 1
                if not prefix_parts:
                    k = i - 1
                    collect = []
                    while k >= 0 and len(collect) < 5:
                        s = stmts[k]
                        if re.match(r'\btry\b', s) or re.match(r'\bcatch\b', s) or 'fail(' in s:
                            break
                        if ('assert' in s or 'Assert.' in s):
                            k -= 1
                            continue
                        collect.insert(0, s)
                        k -= 1
                    prefix_parts = collect

            prefix_parts.append(call_stmt)
            related_asserts = []  # list of tuples (idx, stmt)
            for j, s in enumerate(stmts[i+1:], start=i+1):
                # stop scanning when we hit another call to the target method (assume new scenario)
                if re.search(r'\b' + re.escape(mname) + r'\s*\(', s) and not ('assert' in s or 'Assert.' in s):
                        break
                if ('assert' in s or 'Assert.' in s):
                    if assigned_var and re.search(r'\b' + re.escape(assigned_var) + r'\b', s):
                        related_asserts.append((j, s))
                        continue
                    # 若只有 recv，**仅接受对 recv 的裸引用**（不接受 recv.field），
                    # 因为 recv.field 通常是与其它状态/字段有关的断言，不直接与方法返回值相关
                    if recv:
                        # accept bare recv OR method calls on recv (e.g. recv.equals(...))
                        if re.search(r'\b' + re.escape(recv) + r'\b(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*\s*\()', s) \
                           or re.search(r'\b' + re.escape(recv) + r'\b(?!\s*\.)', s):
                            related_asserts.append((j, s))
                        continue
                    # 回退：既没有 assigned_var 也没有 recv 时，接受第一个断言
                    if not assigned_var and not recv:
                        related_asserts.append((j, s))
                        continue
                    # 否则认为该断言与当前调用无关，跳过
                    continue
                # allow scanning through declarations/new/assignments; but if we hit a control flow or return, stop
                if re.search(r'\b(return|if|for|while|try|catch|throw|fail)\b', s):
                    break

            if related_asserts:
                for (a_idx, a_stmt) in related_asserts:
                    if a_idx in used_asserts:
                        continue
                    pre_list = [s for s in stmts[:a_idx] if not ('assert' in s or 'Assert.' in s)]
                    pref = ' '.join(pre_list).strip()
                    results.append({'prefix': pref, 'assert': a_stmt.strip()})
                    used_asserts.add(a_idx)
            else:
                # no related asserts found -> keep the original prefix + empty assert
                results.append({'prefix': ' '.join(prefix_parts).strip(), 'assert': ''})
            break

    # 如果方法要求参数且没有匹配到任何调用，返回空（你要求的行为）
    if m_param_count > 0 and not results:
        return []

    # post-process: special try/fail handling and run(...) adjustments (保留原逻辑)
    if results:
        for r in results:
            if r.get('prefix') and re.search(r'\btry\s*\{', r['prefix']):
                if 'fail(' in test_body:
                    r['prefix'] = re.sub(r'\btry\s*\{', '', r['prefix']).replace('}', '').strip()
                    r['prefix'] = re.sub(r'\n\s*', ' ', r['prefix']).strip()
                    r['assert'] = 'exception'

        # improved run()/Runnable extraction: extract the inner run() body statements (drop asserts)
        for r in results:
            if not r.get('prefix'):
                continue
            pref = r['prefix']
            body_text = None

            # try to find a new Runnable { ... } and then the run() body inside it
            m_new = re.search(r'new\s+Runnable\s*\(\s*\)\s*\{', pref)
            if m_new:
                open_pos = pref.find('{', m_new.start())
                if open_pos != -1:
                    close_pos = find_matching_brace(pref, open_pos)
                    if close_pos:
                        inner = pref[open_pos+1:close_pos]
                        m_run = re.search(r'\brun\s*\([^)]*\)\s*\{', inner)
                        if m_run:
                            run_open = inner.find('{', m_run.start())
                            if run_open != -1:
                                run_close = find_matching_brace(inner, run_open)
                                if run_close:
                                    body_text = inner[run_open+1:run_close]
                    else:
                        # pref may be a truncated fragment (no matching brace). try to locate the run()
                        # body in the full test_body (fallback) by finding pref in test_body and then the run() after it.
                        try:
                            idx_in_test = test_body.find(pref)
                        except Exception:
                            idx_in_test = -1
                        if idx_in_test != -1:
                            m_run_global = re.search(r'\brun\s*\([^)]*\)\s*\{', test_body[idx_in_test:])
                            if m_run_global:
                                run_open_pos = idx_in_test + m_run_global.start()
                                run_brace_pos = test_body.find('{', run_open_pos)
                                if run_brace_pos != -1:
                                    run_close_pos = find_matching_brace(test_body, run_brace_pos)
                                    if run_close_pos:
                                        body_text = test_body[run_brace_pos+1:run_close_pos]
                        else:
                            # as last resort, search for a run() body anywhere in the full test_body
                            m_run_any = re.search(r'\brun\s*\([^)]*\)\s*\{', test_body)
                            if m_run_any:
                                run_brace_pos = test_body.find('{', m_run_any.start())
                                if run_brace_pos != -1:
                                    run_close_pos = find_matching_brace(test_body, run_brace_pos)
                                    if run_close_pos:
                                        body_text = test_body[run_brace_pos+1:run_close_pos]

            # fallback: look for run() { ... } directly in the prefix
            if body_text is None:
                m_run = re.search(r'\brun\s*\([^)]*\)\s*\{', pref)
                if m_run:
                    run_open = pref.find('{', m_run.start())
                    if run_open != -1:
                        run_close = find_matching_brace(pref, run_open)
                        if run_close:
                            body_text = pref[run_open+1:run_close]

            if body_text is not None:
                inner_stmts = [s.strip() for s in re.findall(r'[^;]+;', body_text, flags=re.DOTALL) if s.strip()]
                a = r.get('assert', '')
                # if the assert appears in the run body, keep only statements before it
                if a and a != 'exception' and a in body_text:
                    idx = body_text.find(a)
                    before = []
                    for s in inner_stmts:
                        pos = body_text.find(s)
                        if pos != -1 and pos < idx and not ('assert' in s or 'Assert.' in s):
                            before.append(s.rstrip().strip())
                    if before:
                        r['prefix'] = ' '.join(before).strip()
                        continue
                # otherwise, remove any assert statements and use remaining statements
                non_asserts = [s for s in inner_stmts if not ('assert' in s or 'Assert.' in s)]
                if non_asserts:
                    r['prefix'] = ' '.join([s.rstrip().strip() for s in non_asserts]).strip()

    #新增行为：若某个结果包含 assert（非 'exception'），则把其 prefix 设为该 assert 在测试体中之前的所有语句（并移除其中的其它 assert 语句）
    if results:
        for r in results:
            a = r.get('assert', '')
            if not a or a == 'exception':
                continue

            # 先尝试判断该断言是否位于某个 run() {...} 块内部；若是，仅保留该 run 块中断言之前的语句（并去掉其它 assert）
            pos = test_body.find(a)
            if pos != -1:
                found_in_run = False
                for m in re.finditer(r'\brun\s*\([^)]*\)\s*\{', test_body):
                    run_open = test_body.find('{', m.start())
                    if run_open == -1:
                        continue
                    run_close = find_matching_brace(test_body, run_open)
                    if run_close is None:
                        continue
                    if run_open < pos < run_close:
                        # 提取 run 块并按语句分割
                        block = test_body[run_open+1:run_close]
                        inner_stmts = [s.strip() for s in re.findall(r'[^;]+;', block, flags=re.DOTALL) if s.strip()]
                        # 找到断言在 block 中的相对位置并确定其索引
                        rel_pos = pos - (run_open+1)
                        idx_in_block = -1
                        cur = 0
                        for ii, s in enumerate(inner_stmts):
                            sp = block.find(s, cur)
                            if sp == -1:
                                continue
                            if sp <= rel_pos < sp + len(s):
                                idx_in_block = ii
                                break
                            cur = sp + len(s)
                        if idx_in_block != -1:
                            pre_list = [s for s in inner_stmts[:idx_in_block] if not ('assert' in s or 'Assert.' in s)]
                            if pre_list:
                                r['prefix'] = ' '.join(pre_list).strip()
                                found_in_run = True
                                break
                if found_in_run:
                    continue

            # 回退到原有行为：在顶层语句列表 stmts 中查找断言并取之前的非 assert 语句
            try:
                idx = stmts.index(a)
            except ValueError:
                idx = -1
                for ii, s in enumerate(stmts):
                    if a in s or s in a:
                        idx = ii
                        break
            if idx != -1:
                pre_list = [s for s in stmts[:idx] if not ('assert' in s or 'Assert.' in s)]
                new_pref = ' '.join(pre_list).strip()
                r['prefix'] = new_pref
            else:
                parts = [s for s in re.findall(r'[^;]+;', r.get('prefix','')) if s.strip() and not ('assert' in s or 'Assert.' in s)]
                r['prefix'] = ' '.join([p.strip() for p in parts]).strip()
                
    if results:
        # 检查是否有 assert 为空的结果
        empty_assert_exists = any(r.get('assert', '') == '' for r in results)
        
        if empty_assert_exists:
            # 找到测试体中的所有断言语句
            all_assert_stmts = []
            for idx, stmt in enumerate(stmts):
                if 'assert' in stmt or 'Assert.' in stmt:
                    all_assert_stmts.append((idx, stmt.strip()))
            
            # 如果找到了断言语句，使用最后一个断言
            if all_assert_stmts:
                last_assert_idx, last_assert_stmt = all_assert_stmts[-1]
                
                # 更新所有 assert 为空的结果
                for r in results:
                    if r.get('assert', '') == '':
                        # 获取最后一个断言之前的所有非断言语句
                        prefix_stmts = [ s for idx, s in enumerate(stmts[:last_assert_idx]) if not ('assert' in s or 'Assert.' in s) ]
                        new_prefix = ' '.join(prefix_stmts).strip()
                        
                        # 更新结果
                        r['prefix'] = new_prefix
                        r['assert'] = last_assert_stmt
        for r in results:
            p = r.get('prefix', '')
            if not p:
                continue
            # 不断移除开头到并包含指定标记的部分，直至不再包含任何标记
            while True:
                found = False
                for token in ('void run() {', 'run() {', 'try {'):
                    idx = p.find(token)
                    if idx != -1:
                        p = p[idx + len(token):]
                        found = True
                        break
                if not found:
                    break
            # 去掉开头多余的空白、分号或孤立大括号
            p = re.sub(r'^[\s;{}]+', '', p)
            r['prefix'] = p.strip()
            
    return results
def replace_from_first_brace(file_path, new_code, file_name):
    """
    定位 Java 文件中 public void testX() 的第一个 '{' ，删除该方法体内容并插入 new_code，然后保留一个 '}' 结束该方法。
    如果从 new_code 中无法提取方法名或定位失败，回退到原先的文件首个 '{' 替换行为。
    修改：在找到第一个目标方法并替换后，删除该方法之后的所有内容，并补全缺失的右大括号以保证文件能编译。
    """
    if not os.path.exists(file_path):
        print(f"[!] 文件不存在: {file_path}")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # helper: 计算文本中未匹配的 '{' 数量（跳过注释/字符串）
    def count_unmatched_braces_smart(text: str) -> int:
        in_string = in_char = in_line_comment = in_block_comment = False
        opens = closes = 0
        i = 0
        L = len(text)
        while i < L:
            ch = text[i]
            if in_line_comment:
                if ch == '\n':
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                if ch == '*' and i + 1 < L and text[i+1] == '/':
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue
            if in_string:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '"':
                    in_string = False
                i += 1
                continue
            if in_char:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '\'':
                    in_char = False
                i += 1
                continue
            if ch == '/' and i + 1 < L:
                if text[i+1] == '/':
                    in_line_comment = True
                    i += 2
                    continue
                if text[i+1] == '*':
                    in_block_comment = True
                    i += 2
                    continue
            if ch == '"':
                in_string = True
            elif ch == '\'':
                in_char = True
            elif ch == '{':
                opens += 1
            elif ch == '}':
                closes += 1
            i += 1
        return opens - closes

    # 1) 尝试从 new_code 中提取方法名（例如 public void test0()）
    m = re.search(r'\bvoid\s+([A-Za-z_]\w*)\s*\(', new_code)
    # if m:
    #     mname = m.group(1)
    # else:
    #     # 强制使用 test0 并在写入时删除后续所有 test 方法
    mname = "test0"
    def fallback_replace_first_brace():
        brace_pos = content.find('{')
        if brace_pos == -1:
            print(f"[!] 找不到 '{{' ：{file_path}")
            return
        # 只保留到第一个 '{'，插入 new_code，并补全缺失的右大括号
        new_content = content[:brace_pos + 1] + '\n' + new_code.strip() + '\n}'
        missing = count_unmatched_braces_smart(new_content)
        if missing > 0:
            new_content = new_content + ('}' * missing)
        with open(file_path, 'w', encoding='utf-8') as f2:
            f2.write(new_content)
        return

    if not mname:
        # 无法解析方法名，使用回退逻辑
        fallback_replace_first_brace()
        return

    # 2) 在文件中查找 public void <mname>(...) 的位置
    pattern = re.compile(r'\bpublic\s+void\s+' + re.escape(mname) + r'\s*\([^)]*\)\s*', re.M)
    pm = pattern.search(content)
    if not pm:
        # 未找到指定方法签名，回退到首个 '{'
        fallback_replace_first_brace()
        return

    # 3) 找到方法签名后的第一个实际 '{'（跳过空白与注释/字符串）
    i = pm.end()
    L = len(content)
    in_s = in_c = in_line_comment = in_block_comment = False
    open_brace_pos = None
    while i < L:
        ch = content[i]
        if in_line_comment:
            if ch == '\n':
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == '*' and i + 1 < L and content[i+1] == '/':
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_s:
            if ch == '\\':
                i += 2
                continue
            if ch == '"':
                in_s = False
            i += 1
            continue
        if in_c:
            if ch == '\\':
                i += 2
                continue
            if ch == '\'':
                in_c = False
            i += 1
            continue
        if ch == '/' and i + 1 < L:
            if content[i+1] == '/':
                in_line_comment = True
                i += 2
                continue
            if content[i+1] == '*':
                in_block_comment = True
                i += 2
                continue
        if ch == '"':
            in_s = True
        elif ch == '\'':
            in_c = True
        elif ch == '{':
            open_brace_pos = i
            break
        elif ch.isspace():
            i += 1
            continue
        else:
            i += 1
            continue

    if open_brace_pos is None:
        # 没有找到方法体开始，回退
        fallback_replace_first_brace()
        return

    # 4) 找到匹配的右大括号位置（原方法结束位置）
    method_end = find_matching_brace_smart(content, open_brace_pos + 1)
    if method_end is None:
        print(f"[!] 无法找到与方法起始 '{{' 匹配的 '}}' ：{file_path} (method {mname})")
        return

    # 5) 清理 new_code：若 new_code 含有方法签名（或完整方法），仅提取方法体内部，避免把方法签名/注解插入到已有签名中导致重复
    def extract_inner_body_from_new_code(ncode: str, method_name: str) -> str:
        """
        更稳健地从 new_code 提取方法体：先找到参数列表的匹配 ')'，然后定位真正的方法体 '{'（跳过注释/字符串）。
        回退到原有的第一个真正的 '{' 行为仅在未找到时使用。
        """
        # helper: find matching paren, skipping strings/comments
        def find_matching_paren(text: str, open_pos: int) -> Optional[int]:
            i = open_pos
            depth = 0
            L = len(text)
            in_s = in_c = in_line_comment = in_block_comment = False
            while i < L:
                ch = text[i]
                if in_line_comment:
                    if ch == '\n':
                        in_line_comment = False
                    i += 1; continue
                if in_block_comment:
                    if ch == '*' and i + 1 < L and text[i+1] == '/':
                        in_block_comment = False
                        i += 2; continue
                    i += 1; continue
                if in_s:
                    if ch == '\\':
                        i += 2; continue
                    if ch == '"':
                        in_s = False
                    i += 1; continue
                if in_c:
                    if ch == '\\':
                        i += 2; continue
                    if ch == "'":
                        in_c = False
                    i += 1; continue
                if ch == '/' and i + 1 < L:
                    if text[i+1] == '/':
                        in_line_comment = True; i += 2; continue
                    if text[i+1] == '*':
                        in_block_comment = True; i += 2; continue
                if ch == '"':
                    in_s = True; i += 1; continue
                if ch == '\'':
                    in_c = True; i += 1; continue
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0:
                        return i
                i += 1
            return None

        # 1) 优先按参数列表后面的真实 '{' 提取方法体
        L = len(ncode)
        i = 0
        while True:
            m = re.search(r'\(', ncode[i:])
            if not m:
                break
            paren_start = i + m.start()
            paren_end = find_matching_paren(ncode, paren_start)
            if paren_end is None:
                i = paren_start + 1
                continue
            # 找到在该 ')' 之后的第一个真实 '{'
            brace_pos = find_next_open_brace_smart(ncode, paren_end + 1)
            if brace_pos is not None:
                match_end = find_matching_brace_smart(ncode, brace_pos + 1)
                if match_end is not None:
                    return ncode[brace_pos + 1: match_end - 1].strip()
            i = paren_end + 1

        # 2) 回退：查找含 method_name 的签名并按其后的 '{' 提取
        if method_name:
            name_pat = re.compile(r'\b' + re.escape(method_name) + r'\s*\(', re.M)
            m = name_pat.search(ncode)
            if m:
                brace_pos = find_next_open_brace_smart(ncode, m.end())
                if brace_pos is not None:
                    match_end = find_matching_brace_smart(ncode, brace_pos + 1)
                    if match_end is not None:
                        return ncode[brace_pos + 1: match_end - 1].strip()

        # 3) 最后回退到原来的策略：第一个真实 '{'
        first_br = find_next_open_brace_smart(ncode, 0)
        if first_br is not None:
            match_end = find_matching_brace_smart(ncode, first_br + 1)
            if match_end is not None:
                return ncode[first_br + 1: match_end - 1].strip()
        return ncode.strip()

    new_body = extract_inner_body_from_new_code(new_code, mname)

    # 6) 构建新的文件内容：保留到方法的开括号，插入新的方法体内容，并关闭方法
    new_content = content[:open_brace_pos + 1] + '\n' + new_body + '\n}'
    # 计算需要多少个右大括号来平衡结构
    missing = count_unmatched_braces_smart(new_content)
    if missing > 0:
        # 需要添加缺失的右大括号
        new_content = new_content + ('}' * missing)
    elif missing < 0:
        # 需要移除多余的右大括号
        excess_braces = abs(missing)
        for _ in range(excess_braces):
            last_brace_pos = new_content.rfind('}')
            if last_brace_pos != -1:
                new_content = new_content[:last_brace_pos] + new_content[last_brace_pos+1:]
    
    # 最重要的部分：确保不附加任何原文件中该方法之后的内容
    # 我们只保留从文件开始到目标方法结束的内容
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
def run_cmd_with_timeout(cmd, cwd=None, timeout=CMD_TIMEOUT):
    """带超时的命令执行工具，避免外部命令阻塞"""
    import signal
    try:
        # 使用 Popen 和 process group 来确保可以杀死所有子进程
        with subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=os.setsid
        ) as process:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                print(f"[WARN] 命令超时({timeout}s): {' '.join(cmd)}")
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except Exception:
                    pass
                return None
    except Exception as e:
        print(f"[ERROR] 命令执行异常: {str(e)}")
        return None
def parse_junit_output(output: str) -> dict:
    """
    解析 JUnit 控制台输出，返回摘要：
      { "ok": True/False/None, "run": int|None, "failures": int|None, "raw": output }
    能识别常见格式，例如:
      "OK (9 tests)"
      "FAILURES!!!"
      "Tests run: 9,  Failures: 1,  Errors: 0"
    """
    summary = {"ok": None, "run": None, "failures": None, "raw": output}
    if not output:
        return summary
    lines = [l.strip() for l in output.splitlines() if l.strip() != ""]
    # 从末尾向前找，以便匹配 summary 行
    for line in reversed(lines):
        m = re.search(r'OK\s*\(\s*(\d+)\s+tests?\s*\)', line, re.I)
        if m:
            summary["ok"] = True
            summary["run"] = int(m.group(1))
            summary["failures"] = 0
            return summary
        if 'FAILURES!!!' in line or 'FAILURES' == line:
            summary["ok"] = False
            # 继续看看是否能找到 Tests run 行来填充 numbers
            continue
        m2 = re.search(r'Tests run[:=]\s*(\d+)\s*,\s*Failures[:=]\s*(\d+)', line, re.I)
        if m2:
            summary["run"] = int(m2.group(1))
            summary["failures"] = int(m2.group(2))
            summary["ok"] = (summary["failures"] == 0)
            return summary
        # 有些 JUnit 版本/runner 会输出 "There was 1 failure:"
        m3 = re.search(r'There (?:was|were)\s+(\d+)\s+fail', line, re.I)
        if m3:
            summary["failures"] = int(m3.group(1))
            summary["ok"] = (summary["failures"] == 0)
            return summary
    return summary        
def get_jacoco_path(source_file_path, class_name, report_type="html"):
    """
    获取JaCoCo报告路径（支持HTML和XML格式）
    
    Args:
        source_file_path: Java源文件路径
        class_name: 完整类名
        report_type: 报告类型，"html" 或 "xml"
    
    Returns:
        报告文件路径
    """
    # 将路径字符串转换为Path对象
    source_path = Path(source_file_path)
    
    # 查找可能的源代码目录模式
    src_patterns = [
        "src/main/java",
        "src/java",
        "src"
    ]
    
    # 查找项目根目录
    project_root = None
    for pattern in src_patterns:
        try:
            # 找到src目录在路径中的位置
            parts = list(source_path.parts)
            if pattern in parts:
                # 找到pattern的位置
                idx = parts.index(pattern.split('/')[0])
                # 获取项目根目录
                project_root = Path(*parts[:idx])
                break
        except (ValueError, IndexError):
            continue
    
    if project_root is None:
        # 如果没找到标准模式，尝试基于类名推断
        # 将类名转换为路径
        class_path = class_name.replace('.', '/')
        # 从源文件路径中移除类路径部分
        source_str = str(source_path)
        class_file = f"{class_path}.java"
        if source_str.endswith(class_file):
            project_root = Path(source_str[:-len(class_file)])
            # 尝试找到实际的src目录
            for pattern in ["src/main/java/", "src/java/", "src/"]:
                if pattern in str(project_root):
                    project_root = Path(str(project_root).split(pattern)[0])
    
    if project_root is None:
        raise ValueError(f"无法从路径 {source_file_path} 推断项目根目录")
    
    # 构建报告路径
    class_path = class_name.replace('.', '/')
    # print(class_path)
    if report_type == "html":
        report_path = project_root / "report" / "jacoco-report" / f"{class_path}.java.html"
    elif report_type == "xml":
        report_path = project_root / "report" / "jacoco-report" / f"{class_path}.xml"
    else:
        raise ValueError(f"不支持的报告类型: {report_type}")
    # print(report_path)
    return str(report_path)
def find_next_open_brace_smart(text: str, start_pos: int) -> Optional[int]:
    """
    从 start_pos 起，跳过字符串/注释，找到第一个真实的 '{' 的索引。
    """
    in_string = in_char = in_line_comment = in_block_comment = False
    i = start_pos
    L = len(text)
    while i < L:
        ch = text[i]
        if in_line_comment:
            if ch == '\n':
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == '*' and i + 1 < L and text[i+1] == '/':
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string:
            if ch == '\\':
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if in_char:
            if ch == '\\':
                i += 2
                continue
            if ch == '\'':
                in_char = False
            i += 1
            continue

        if ch == '/' and i + 1 < L:
            if text[i+1] == '/':
                in_line_comment = True
                i += 2
                continue
            if text[i+1] == '*':
                in_block_comment = True
                i += 2
                continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == '\'':
            in_char = True
            i += 1
            continue

        if ch == '{':
            return i
        i += 1
    return None
def get_execution_paths(focal_method, html_path):
    """
    使用 BeautifulSoup 解析 focal_method，在 JaCoCo HTML 中定位对应方法，
    返回该方法内被执行的代码行（覆盖标记 fc/pc 的行文本列表）。
    """
    if not focal_method or not html_path or not os.path.exists(html_path):
        return []
    
    try:
        # 1) AST 获取方法名与参数类型
        tree = javalang.parse.parse(java_template.format(code=focal_method))
        mnode = None
        if tree.types and tree.types[0].methods:
            mnode = tree.types[0].methods[0]
        elif tree.types and tree.types[0].constructors:
            mnode = tree.types[0].constructors[0]
        
        if not mnode:
            print("[WARN] 无法从 AST 解析出方法或构造函数节点")
            return []

        mname = mnode.name
        param_cnt = len(mnode.parameters)
        param_types = []
        for p in mnode.parameters:
            tname = getattr(p.type, "name", str(p.type))
            param_types.append(tname.split(".")[-1])
    except Exception as e:
        print(f"[WARN] AST解析失败: {e}")
        return []
    try:
        # 2) 使用 BeautifulSoup 读取并解析 HTML
        with open(html_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f, 'html.parser')
        # 3) 提取所有代码行的覆盖信息
        lines_map = {}
        # 查找所有带 id="L数字" 的 span 标签
        for tag in soup.find_all(attrs={'id': re.compile(r'^L\d+$')}):
            span_id = tag.get('id', '')
            try:
                lnum = int(span_id[1:])  # 移除 'L' 前缀获取行号
            except Exception:
                continue

            # 获取覆盖类型（尝试从当前标签或其 class 属性中获取）
            classes = tag.get('class', []) or []
            cov = 'nc'  # 默认未覆盖
            for cls in classes:
                if cls in ('fc', 'pc', 'nc'):
                    cov = cls
                    break
            # 有时候覆盖类在子标签中，检查子节点的 class
            if cov == 'nc':
                child = tag.find(class_=re.compile(r'^(fc|pc|nc)$'))
                if child:
                    child_classes = child.get('class', []) or []
                    for cls in child_classes:
                        if cls in ('fc', 'pc', 'nc'):
                            cov = cls
                            break

            # 获取行文本（回退到父行所在的 pre 行文本）
            line_text = tag.get_text() if tag.get_text() is not None else ''
            line_text = html.unescape(line_text).rstrip('\n\r')
            if line_text.strip() != "":
                lines_map[lnum] = (cov, line_text)
        pre = soup.find('pre', class_='source')
        if pre:
            pre_text = html.unescape(pre.get_text() or "")
            pre_lines = pre_text.splitlines()
            if pre_lines:
                for i, pl in enumerate(pre_lines, start=1):
                    if i not in lines_map:
                        # 默认为未覆盖 'nc'
                        lines_map[i] = ('nc', pl.rstrip('\n\r'))        
        if not lines_map:
            print(f"[WARN] 未在 HTML 中找到任何代码行")
            return []
        
        # 4) 重建源码
        max_line = max(lines_map.keys())
        src_lines = [lines_map.get(i, ("nc", ""))[1] for i in range(1, max_line + 1)]
        cov_lines = [lines_map.get(i, ("nc", ""))[0] for i in range(1, max_line + 1)]
        full_src = "\n".join(src_lines)
        
        # 5) 查找方法所在的行
        method_line_idx = -1
        
        # 首先尝试在被标记的行中查找方法名
        for idx, line in enumerate(src_lines):
            if mname in line:
                print(f"找到包含方法名的行 {idx+1}: {line.strip()}")  # 调试信息
                # 向后合并最多 10 行来获取完整签名
                combined = line
                for j in range(idx + 1, min(idx + 10, len(src_lines))):
                    combined += " " + src_lines[j].strip()
                    if '{' in src_lines[j]:
                        break
                
                print(f"合并后的内容: {combined}")  # 调试信息
                # 检查是否包含开括号（确认是方法声明而不是方法调用）
                if '(' in combined:
                    # 检查是否匹配方法签名
                    sig_pattern = re.compile(
                        r'(?:static\s+)?(?:public\s+)?(?:private\s+)?(?:protected\s+)?(?:final\s+)?'
                        r'(?:[\w<>\[\],\.\s]*?)\s+'
                        r'\b' + re.escape(mname) + r'\s*\(',
                        re.IGNORECASE | re.DOTALL
                    )
                    
                    if sig_pattern.search(combined):
                        method_line_idx = idx
                        print(f"成功匹配方法签名，索引: {method_line_idx}")  # 调试信息
                        break
        
        if method_line_idx == -1:
            # 如果在被标记的行中未找到方法名，尝试在整个源码中搜索
            # 方法声明可能没有被 JaCoCo 覆盖标记，所以不在 lines_map 中
            print(f"[INFO] 在被标记的行中未找到方法 {mname}，在完整源码中搜索")
            full_src_lines = full_src.split('\n')
            for idx, line in enumerate(full_src_lines):
                if mname in line and '(' in line and ')' in line:
                    print(f"在完整源码中找到候选行 {idx+1}: {line.strip()}")  # 调试信息
                    # 检查是否是方法签名而非方法调用
                    combined = line
                    # 向后查找，看是否有 '{' 或 ';'
                    for j in range(idx + 1, min(idx + 10, len(full_src_lines))):
                        combined += " " + full_src_lines[j].strip()
                        if '{' in full_src_lines[j] or ';' in full_src_lines[j]:
                            break
                    
                    sig_pattern = re.compile(
                        r'(?:static\s+)?(?:public\s+)?(?:private\s+)?(?:protected\s+)?(?:final\s+)?'
                        r'(?:[\w<>\[\],\.\s]*?)\s+'
                        r'\b' + re.escape(mname) + r'\s*\(',
                        re.IGNORECASE | re.DOTALL
                    )
                    
                    if sig_pattern.search(combined):
                        # 将完整源码中的行号转换为标记源码中的行号
                        # 由于 src_lines 是从 1 开始的，所以直接使用 idx 即可
                        # 但需要确保行在 src_lines 范围内
                        if idx < len(src_lines):
                            method_line_idx = idx
                            print(f"在完整源码中成功匹配方法签名，索引: {method_line_idx}")  # 调试信息
                            break

        if method_line_idx == -1:
            # 如果仍然无法在单行中定位方法名，改为全文范围搜索方法签名（方法名可能被 HTML 拆分到多行）
            print(f"[WARN] 源码中未在单行定位到方法 {mname}, 将使用全文签名搜索")
            # 搜索整个源码来查找方法定义
            for idx, line in enumerate(src_lines):
                # 检查这行是否包含方法定义，不只是方法名
                combined = ""
                # 向前和向后查找，构建完整的方法签名
                for j in range(max(0, idx - 5), min(len(src_lines), idx + 10)):
                    combined += " " + src_lines[j].strip()
                    if '{' in src_lines[j] or ';' in src_lines[j]:  # 方法体开始或方法声明结束
                        break
                
                sig_pattern = re.compile(
                    r'(?:static\s+)?(?:public\s+)?(?:private\s+)?(?:protected\s+)?(?:final\s+)?'
                    r'(?:[\w<>\[\],\.\s]*?)\s+'
                    r'\b' + re.escape(mname) + r'\s*\(',
                    re.IGNORECASE | re.DOTALL
                )
                
                if sig_pattern.search(combined):
                    method_line_idx = idx
                    print(f"全文搜索找到方法 {mname} 在索引 {idx}")  # 调试信息
                    break

        if method_line_idx == -1:
            print(f"[ERROR] 无法在HTML中找到方法 {mname}")  # 调试信息
            return []

        # 6) 定位完整方法签名
        def extract_param_types(params_text: str) -> list:
            """
            更健壮地从方法参数签名中提取类型名称（去掉注解、final、变量名、泛型、数组与可变参数标记）。
            返回简单类型名列表（例如 List<String> -> List, String... -> String）。
            """
            if not params_text or params_text.strip() == "":
                return []
                
            # 从参数文本中移除throws子句及其后的内容
            params_text = params_text.split('throws')[0].strip()
            
            # 拆分顶层逗号（跳过泛型内部逗号）
            parts = []
            depth = 0
            cur = []
            for ch in params_text:
                if ch == '<':
                    depth += 1
                elif ch == '>':
                    if depth > 0: depth -= 1
                if ch == ',' and depth == 0:
                    parts.append(''.join(cur).strip())
                    cur = []
                    continue
                cur.append(ch)
            if cur:
                parts.append(''.join(cur).strip())

            types = []
            for p in parts:
                # 移除注解（@...）和修饰符（final等）
                p2 = re.sub(r'@\w+(?:\([^)]*\))?\s*', '', p)         # 注解
                p2 = re.sub(r'\b(final|volatile|transient)\b\s*', '', p2)
                p2 = p2.strip()
                if not p2:
                    continue
                # 去掉可能的赋值（unlikely in params but be safe）
                p2 = p2.split('=')[0].strip()
                # 捕获类型部分（尽量取参数声明中除了最后标识符之外的部分）
                m = re.match(r'^(.*\S)\s+[A-Za-z_$][A-Za-z0-9_$]*(\s*\[\s*\])?$', p2)
                type_part = m.group(1) if m else p2
                type_part = type_part.strip()
                # 去掉可变参数标识 ...
                type_part = type_part.replace('...', '')
                # 去掉数组符号
                type_part = re.sub(r'\[\s*\]', '', type_part)
                # 去掉方法类型参数和泛型实参 (Handle nested generics)
                while '<' in type_part:
                    start_idx = type_part.find('<')
                    balance = 0
                    end_idx = -1
                    for k in range(start_idx, len(type_part)):
                        if type_part[k] == '<':
                            balance += 1
                        elif type_part[k] == '>':
                            balance -= 1
                        if balance == 0:
                            end_idx = k
                            break
                    if end_idx != -1:
                        type_part = type_part[:start_idx] + type_part[end_idx+1:]
                    else:
                        break # Unbalanced
                        
                # 取最后的简单类名（按 . 分割）
                simple = type_part.split('.')[-1].strip()
                # 如果还是空则跳过
                if simple:
                    types.append(simple)
            return types

        target_start = target_brace = None
        
        search_start = max(0, method_line_idx - 10)
        search_text = "\n".join(src_lines[search_start:])
        
        search_offset = 0
        for i in range(search_start):
            search_offset += len(src_lines[i]) + 1
        
        # 修复正则表达式，更准确地匹配方法签名，包括throws子句
        # 使用更灵活的返回类型匹配，支持复杂的泛型（如 Map<A, B>）和构造函数（无返回类型）
        sig_re = re.compile(
            r'(?:static\s+)?(?:public\s+)?(?:private\s+)?(?:protected\s+)?(?:final\s+)?'
            # 匹配返回类型部分：可以是任意类名、基本类型、泛型结构、数组，允许空格和逗号
            # 非贪婪匹配，直到遇到方法名后的 (
            r'(?:[\w<>\[\],\.\s]*?)\s+'
            r'\b' + re.escape(mname) + r'\s*\(\s*([^)]*?)\s*\)\s*(?:throws\s+[^{;]*)?[{;]',
            re.DOTALL | re.IGNORECASE
        )
        
        matches = list(sig_re.finditer(search_text))
        print(f"找到 {len(matches)} 个方法签名匹配")  # 调试信息
        
        for idx, m in enumerate(matches):
            params_text = m.group(1)
            print(f"参数文本: '{params_text}'")  # 调试信息
            types_found = extract_param_types(params_text)
            print(f"提取的参数类型: {types_found}")  # 调试信息
            
            if len(types_found) != param_cnt:
                print(f"参数数量不匹配: {len(types_found)} != {param_cnt}")  # 调试信息
                continue
            
            if types_found == param_types:
                print(f"参数类型匹配成功")  # 调试信息
                target_start = search_offset + m.start()

                # 正确确定方法体的开括号：
                # 注意：sig_re 会把 '{' 包含在匹配末尾（如果存在），因此优先使用它
                local_end = m.end()
                consumed_char = search_text[local_end - 1] if local_end - 1 < len(search_text) else ''
                if consumed_char == '{':
                    # 方法签名已包含 '{'，直接定位该 '{'
                    target_brace = search_offset + local_end - 1
                    print(f"找到方法体起始位置(签名包含'{{'): {target_brace}")  # 调试信息
                else:
                    # 否则，再寻找下一个真实的 '{'
                    brace_pos_in_full = find_next_open_brace_smart(full_src, search_offset + local_end)
                    if brace_pos_in_full is not None:
                        target_brace = brace_pos_in_full
                        print(f"找到方法体起始位置(智能): {target_brace}")  # 调试信息
                    else:
                        # 回退：保留旧逻辑
                        brace_pos = search_text.find('{', local_end)
                        if brace_pos != -1:
                            target_brace = search_offset + brace_pos
                            print(f"找到方法体起始位置(回退): {target_brace}")  # 调试信息
                        else:
                            target_brace = search_offset + local_end - 1
                            print("未找到方法体起始位置，使用签名末尾回退")
                break
            else:
                print(f"参数类型不匹配: {types_found} != {param_types}")  # 调试信息
                if target_start is None:
                    target_start = search_offset + m.start()
                    brace_pos = search_text.find('{', m.end())
                    if brace_pos != -1:
                        target_brace = search_offset + brace_pos
                    else:
                        target_brace = search_offset + m.end() - 1

        if target_start is None or target_brace is None:
            print(f"[WARN] 未找到匹配的方法签名")
            print(f"  搜索方法: {mname}({', '.join(param_types)})")
            print(f"  HTML 文件: {html_path}")
            return []

        # 7) 匹配方法结束位置
        end_pos = find_matching_brace_smart(full_src, target_brace + 1)
        if end_pos is None:
            print(f"[WARN] 无法找到方法的结束括号")
            return []
        
        start_line = full_src[:target_start].count("\n") + 1
        end_line = full_src[:end_pos].count("\n") + 1
        print(f"方法范围: 第 {start_line} 行到第 {end_line} 行")  # 调试信息

        # 去掉仅向后最多扩 10 行且遇到 nc 就停止的扩展逻辑，直接以方法闭合行为准
        # 收集覆盖的行
        executed_lines = []
        covered_indices = [i for i in range(start_line - 1, min(end_line, len(cov_lines)))
                           if cov_lines[i] in ("fc", "pc")]
        print(f"覆盖的行索引: {covered_indices}")  # 调试信息
        for ci in covered_indices:
            cov = cov_lines[ci] if ci < len(cov_lines) else "<missing>"
            line_txt = src_lines[ci] if ci < len(src_lines) else "<no-src>"
            print(f"DBG idx={ci+1} cov={cov} line={repr(line_txt)}")

        # 合并相邻覆盖行形成语句片段
        i = 0
        while i < len(covered_indices):
            idx = covered_indices[i]
            buf = src_lines[idx].rstrip()
            i += 1
            while i < len(covered_indices):
                next_idx = covered_indices[i]
                if next_idx != idx + 1:
                    break
                next_line = src_lines[next_idx].strip()
                if buf.rstrip().endswith((';', '}')):
                    break
                # 简化启发式：若当前行未以结束符结尾或括号不平衡，则继续合并
                join_needed = (buf.count('(') > buf.count(')')) or (not buf.rstrip().endswith((';', '}')))
                if not join_needed:
                    break
                buf = buf.rstrip() + ' ' + next_line
                idx = next_idx
                i += 1
            executed_lines.append(buf.strip())

        return executed_lines
        
    except Exception as e:
        print(f"[ERROR] 处理异常: {e}")
        traceback.print_exc()
        return []
def find_and_replace_method_ast(content: str, class_name: str, method_name: str, new_code: str) -> Tuple[Optional[str], Optional[str]]:
    """
    更稳健的 AST 定位 + 文本回退方法替换。
    成功返回 (modified_content, original_method_code)，失败返回 (None, None)。
    支持内部类和处理 @Override 等注解。
    """
    try:
        tree = javalang.parse.parse(content)
        
        # 解析传入的新方法代码，试图得到 MethodDeclaration 用于精确匹配
        new_method = None
        try:
            wrapper = "class __Dummy__ { " + new_code + " }"
            tmp = javalang.parse.parse(wrapper)
            for _, m in tmp.filter(javalang.tree.MethodDeclaration):
                new_method = m
                break
        except Exception:
            new_method = None

        target_node = None
        
        # 递归查找目标类（支持内部类）
        def find_class_recursive(node, target_name):
            if isinstance(node, javalang.tree.ClassDeclaration) and node.name == target_name:
                return node
            if hasattr(node, 'body') and node.body:
                for item in node.body:
                    if isinstance(item, javalang.tree.ClassDeclaration):
                        result = find_class_recursive(item, target_name)
                        if result:
                            return result
            return None
        
        # 新增：递归查找所有类中的目标方法
        def find_method_in_all_classes(node, target_method_name):
            """
            递归遍历所有类（包括内部类），查找指定名称的方法
            返回 (class_node, method_node) 或 (None, None)
            """
            if isinstance(node, javalang.tree.ClassDeclaration):
                # 在当前类中查找方法
                for method in node.methods:
                    if method.name == target_method_name and getattr(method, "position", None):
                        return node, method
                # 递归查找内部类
                if hasattr(node, 'body') and node.body:
                    for item in node.body:
                        if isinstance(item, javalang.tree.ClassDeclaration):
                            result_class, result_method = find_method_in_all_classes(item, target_method_name)
                            if result_method:
                                return result_class, result_method
            elif hasattr(node, 'types'):
                # 处理编译单元（根节点）
                for type_decl in node.types:
                    result_class, result_method = find_method_in_all_classes(type_decl, target_method_name)
                    if result_method:
                        return result_class, result_method
            return None, None
        
        target_class = find_class_recursive(tree, class_name)
        
        # 如果未找到指定类名，尝试递归查找方法
        if not target_class:
            print(f"[INFO] 未找到类 {class_name}，尝试在所有类中递归查找方法 {method_name}")
            found_class, found_method = find_method_in_all_classes(tree, method_name)
            if found_method:
                print(f"[INFO] 在类 {found_class.name} 中找到方法 {method_name}")
                target_class = found_class
                target_node = found_method
            else:
                print(f"[ERROR] 在整个文件中未找到方法 {method_name}")
                return None, None
        
        # 如果找到了目标类但还没有确定目标方法，继续原有逻辑
        if target_class and not target_node:
            # 在目标类中收集候选并按签名匹配
            candidates = [m for m in target_class.methods if m.name == method_name and getattr(m, "position", None)]

            # 如果解析到了要插入的新方法，优先按参数数量/类型/名字匹配
            if new_method and candidates:
                nm_params = getattr(new_method, "parameters", []) or []
                # 先按参数个数筛
                same_count = [m for m in candidates if len(getattr(m, "parameters", []) or []) == len(nm_params)]
                if len(same_count) == 1:
                    target_node = same_count[0]
                else:
                    def params_match(a, b):
                        pa = getattr(a, "parameters", []) or []
                        pb = getattr(b, "parameters", []) or []
                        if len(pa) != len(pb):
                            return False
                        for xa, xb in zip(pa, pb):
                            ta = getattr(xa, "type", None)
                            tb = getattr(xb, "type", None)
                            na = getattr(ta, "name", None) if ta else None
                            nb = getattr(tb, "name", None) if tb else None
                            if na and nb and na != nb:
                                return False
                            if getattr(xa, "name", None) and getattr(xb, "name", None) and xa.name != xb.name:
                                return False
                        return True

                    matched = [m for m in (same_count or candidates) if params_match(m, new_method)]
                    if matched:
                        target_node = matched[0]

            # 回退策略：若没有new_method或未匹配上，使用位置距离选择最靠近的同名方法
            if not target_node:
                if not candidates:
                    # 如果没有position的候选，再次尝试递归查找
                    print(f"[INFO] 在类 {target_class.name} 中未找到方法 {method_name}，尝试递归查找")
                    _, found_method = find_method_in_all_classes(tree, method_name)
                    if found_method:
                        target_node = found_method
                    else:
                        return None, None
                else:
                    # 文本中方法名出现的位置列表
                    all_matches = [m.start() for m in re.finditer(r'\b' + re.escape(method_name) + r'\s*\(', content)]
                    if not all_matches:
                        target_node = candidates[0]
                    else:
                        lines = content.splitlines(True)
                        def pos_to_index(pos):
                            return sum(len(lines[i]) for i in range(pos.line - 1)) + (pos.column - 1)
                        best = None
                        best_dist = None
                        for cand in candidates:
                            cand_idx = pos_to_index(cand.position)
                            dist = min(abs(cand_idx - midx) for midx in all_matches)
                            if best is None or dist < best_dist:
                                best = cand
                                best_dist = dist
                        target_node = best

        if not target_node or not getattr(target_node, "position", None):
            return None, None

        # AST 行列 -> 字符索引
        lines = content.splitlines(True)
        line_idx = target_node.position.line - 1
        col_idx = target_node.position.column - 1
        base_offset = sum(len(lines[i]) for i in range(line_idx))
        ast_pos = base_offset + col_idx

        # 方法签名附近搜索 method_name 出现，选最靠近 ast_pos 的一个
        search_back = 2000
        search_start = max(0, ast_pos - search_back)
        search_end = min(len(content), ast_pos + 800)
        window = content[search_start:search_end]
        m_iter = list(re.finditer(r'\b' + re.escape(method_name) + r'\s*\(', window))
        if not m_iter:
            return None, None
        # 选择距离 ast_pos 最近的匹配
        candidates_pos = [(search_start + m.start(), m) for m in m_iter]
        candidates_pos.sort(key=lambda t: abs(t[0] - ast_pos))
        sig_paren_pos = candidates_pos[0][0]

        # 找到参数列表结束位置（配对括号，跳过注释/字符串）
        paren_i = content.find('(', sig_paren_pos)
        if paren_i == -1:
            return None, None
        depth = 0
        i = paren_i
        in_s = in_c = in_line_comment = in_block_comment = False
        L = len(content)
        while i < L:
            ch = content[i]
            if in_line_comment:
                if ch == '\n':
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                if ch == '*' and i + 1 < L and content[i+1] == '/':
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue
            if in_s:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '"':
                    in_s = False
                i += 1
                continue
            if in_c:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '\'':
                    in_c = False
                i += 1
                continue
            if ch == '/' and i+1 < L:
                if content[i+1] == '/':
                    in_line_comment = True
                    i += 2
                    continue
                if content[i+1] == '*':
                    in_block_comment = True
                    i += 2
                    continue
            if ch == '"':
                in_s = True
            elif ch == '\'':
                in_c = True
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if depth != 0:
            return None, None
        paren_end = i

        sig_region_end = paren_end + 1

        # 首先尝试找到最接近签名前的完整修饰符位置
        modifiers = ['public','protected','private','static','final','synchronized','abstract','native','strictfp','transient','volatile']
        mod_pattern = re.compile(r'\b(?:' + '|'.join(modifiers) + r')\b')
        last_mod = None
        for mm in mod_pattern.finditer(content, max(0, sig_paren_pos - 2000), sig_paren_pos):
            last_mod = mm
        if last_mod:
            mod_pos = last_mod.start()
            method_line_start = content.rfind('\n', 0, mod_pos)
            method_line_start = 0 if method_line_start == -1 else method_line_start + 1
            tentative_start = method_line_start
        else:
            # 若未找到修饰符，退回到签名所在行的行首
            line_start = content.rfind('\n', 0, sig_paren_pos)
            tentative_start = 0 if line_start == -1 else line_start + 1

        # 处理可能的"被截断的修饰符/返回类型 token"
        token_left = sig_paren_pos - 1
        while token_left >= 0 and content[token_left].isspace():
            token_left -= 1
        if token_left >= 0 and (content[token_left].isalpha() or content[token_left] == '_'):
            # 回退到 token 开始
            token_start = token_left
            while token_start > 0 and (content[token_start-1].isalpha() or content[token_start-1] == '_' or content[token_start-1].isdigit()):
                token_start -= 1
            token_line_start = content.rfind('\n', 0, token_start)
            token_line_start = 0 if token_line_start == -1 else token_line_start + 1
            # 选择更早的行首
            if token_line_start < tentative_start:
                tentative_start = token_line_start

        # 向上包含注解（@Override 等）和 Javadoc
        start_pos = tentative_start
        while True:
            if start_pos == 0:
                break
            prev_nl = content.rfind('\n', 0, start_pos-1)
            prev_line_start = 0 if prev_nl == -1 else prev_nl + 1
            prev_line = content[prev_line_start:start_pos].strip()
            if prev_line == '':
                break
            # 匹配注解（包括参数）
            if prev_line.startswith('@'):
                start_pos = prev_line_start
                continue
            # 匹配 Javadoc 或块注释
            if prev_line.endswith('*/'):
                comment_begin = content.rfind('/**', 0, start_pos)
                if comment_begin == -1:
                    comment_begin = content.rfind('/*', 0, start_pos)
                if comment_begin != -1:
                    start_pos = comment_begin
                    continue
            # 否则停止扩展
            break

        method_start = start_pos

        # 查找方法体开始或分号（抽象）
        sidx = sig_region_end
        while sidx < len(content) and content[sidx].isspace():
            sidx += 1
        body_brace_idx = None
        if sidx < len(content) and content[sidx] == '{':
            body_brace_idx = sidx
        else:
            # 扫描直到找到 '{' 或 ';'
            i = sig_region_end
            in_s = in_c = in_line_comment = in_block_comment = False
            method_end = None
            while i < len(content):
                ch = content[i]
                if in_line_comment:
                    if ch == '\n':
                        in_line_comment = False
                    i += 1
                    continue
                if in_block_comment:
                    if ch == '*' and i+1 < len(content) and content[i+1] == '/':
                        in_block_comment = False
                        i += 2
                        continue
                    i += 1
                    continue
                if in_s:
                    if ch == '\\':
                        i += 2
                        continue
                    if ch == '"':
                        in_s = False
                    i += 1
                    continue
                if in_c:
                    if ch == '\\':
                        i += 2
                        continue
                    if ch == '\'':
                        in_c = False
                    i += 1
                    continue
                if ch == '/' and i+1 < len(content):
                    if content[i+1] == '/':
                        in_line_comment = True
                        i += 2
                        continue
                    if content[i+1] == '*':
                        in_block_comment = True
                        i += 2
                        continue
                if ch == '{':
                    body_brace_idx = i
                    break
                if ch == ';':
                    method_end = i + 1
                    break
                i += 1
            if body_brace_idx is None and method_end is None:
                return None, None

        if body_brace_idx is not None:
            method_end = find_matching_brace_smart(content, body_brace_idx + 1)
            if method_end is None:
                return None, None

        # 保存原始方法（包含注解/Javadoc）
        original_method_code = content[method_start:method_end]

        # 检查 new_code 是否包含注解或 Javadoc，如果是，则从 method_start (包括注解) 开始替换
        # 否则从 tentative_start (仅方法修饰符开始) 替换，以保留原有的注解/Javadoc
        new_code_stripped = new_code.lstrip()
        if new_code_stripped.startswith(('@', '/**', '/*')):
             replace_start = method_start
        else:
             replace_start = tentative_start

        # 准备新代码并替换
        new_code_clean = new_code.strip()
        insert = "\n" + new_code_clean + "\n"
        modified_content = content[:replace_start] + insert + content[method_end:]

        # 清理可能残留的孤立修饰符行
        clean_region_start = max(0, method_start - 300)
        prefix = modified_content[clean_region_start:method_start]
        cleaned_prefix = re.sub(r'(?m)^[ \t]*(public|protected|private|static|final|synchronized|abstract|native|strictfp|transient|volatile)[ \t]*\r?\n', '', prefix)
        if cleaned_prefix != prefix:
            modified_content = modified_content[:clean_region_start] + cleaned_prefix + modified_content[method_start:]

        return modified_content, original_method_code

    except Exception:
        traceback.print_exc()
        return None, None
    
def replace_method_in_java_file(main_method_path, extracted_class_name, extracted_method_name, new_code):
    """
    在Java文件中找到指定方法并替换为新代码，同时保存原始代码
    """
    if not os.path.exists(main_method_path):
        return {"success": False, "error": "Java文件不存在", "original_code": None}
    
    try:
        # 读取原文件内容
        with open(main_method_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
        
        print(f"处理文件: {main_method_path}")
        print(f"目标类名: {extracted_class_name}")
        print(f"目标方法名: {extracted_method_name}")
        
        # 直接使用新代码，不进行额外的清理（因为清理已经在find_and_replace_method中完成）
        # 查找并替换方法
        modified_content, original_method_code = find_and_replace_method_ast(
            original_content, extracted_class_name, extracted_method_name, new_code
        )
        
        if modified_content is None:
            return {"success": False, "error": "未找到指定方法", "original_code": None,"backup_file": original_content,}
        
        # # 语法验证步骤
        # validation_result = validate_java_syntax(modified_content, main_method_path)
        # if not validation_result["success"]:
        #     print(f"语法验证失败详情:")
        #     print(f"错误: {validation_result['error']}")
        #     print(f"详细信息: {validation_result.get('details', '无详细信息')}")
        #     return {
        #         "success": False, 
        #         "error": f"语法验证失败: {validation_result['error']}", 
        #         "original_code": None,
        #         "validation_details": validation_result
        #     }
        
        # 写入修改后的内容
        with open(main_method_path, 'w', encoding='utf-8') as f:
            f.write(modified_content)
        
        return {
            "success": True, 
            "error": None, 
            "original_code": original_method_code,
            "backup_file": original_content,
            #"validation_result": validation_result
        }
        
    except Exception as e:
        print(f"处理文件时出错: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": f"处理文件时出错: {str(e)}", "original_code": None}
def restore_java_file_from_content(main_method_path, original_content):
    """
    通过原始文件内容直接恢复Java文件到原始状态
    
    Args:
        main_method_path (str): Java文件路径
        original_content (str): 原始文件内容
    
    Returns:
        dict: 操作结果
    """
    try:
        # 检查文件路径是否存在
        if not main_method_path:
            return {
                "success": False, 
                "error": "文件路径为空",
                "restored_path": None
            }
        
        # 创建备份当前文件（如果存在）
        current_backup = None
        if os.path.exists(main_method_path):
            current_backup = main_method_path + ".backup_restore"
            shutil.copy2(main_method_path, current_backup)
        
        # 将原始内容写入文件
        with open(main_method_path, 'w', encoding='utf-8') as f:
            f.write(original_content)
        
        return {
            "success": True, 
            "error": None,
            "restored_path": main_method_path,
            "current_backup": current_backup,
            "message": "文件已成功恢复到原始状态"
        }
        
    except Exception as e:
        return {
            "success": False, 
            "error": f"恢复文件时出错: {str(e)}",
            "restored_path": None
        }    
def run_evo_suite_with_timeout(cmd, cwd=None, timeout=CMD_TIMEOUT):
    """专门处理EvoSuite的命令执行，实时打印输出并超时终止"""
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        start_time = time.time()
        output_lines = []
        while True:
            if time.time() - start_time > timeout:
                proc.kill()
                print(f"[WARN] EvoSuite超时({timeout}s)，已终止")
                return False
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                output_lines.append(line.strip())
        return proc.returncode == 0
    except Exception as e:
        if proc:
            proc.kill()
        print(f"[ERROR] EvoSuite执行异常: {str(e)}")
        return False
def _extract_first_assert(s: str) -> Optional[str]:
    # 从后往前扫描，按括号计数提取最后一个 assert 语句
    matches = list(re.finditer(r'(assertTrue|assert|assertEquals|assertNotNull|assertFalse|assertSame|assertNotSame)\s*\(', s))
    if not matches:
        return None
    
    # 从最后一个匹配开始处理
    for m in reversed(matches):
        start = m.start()
        i = m.end() - 1
        depth = 0
        in_s = in_c = False
        L = len(s)
        while i < L:
            ch = s[i]
            if in_s:
                if ch == '\\':
                    i += 2; continue
                if ch == '"':
                    in_s = False
                i += 1; continue
            if in_c:
                if ch == '\\':
                    i += 2; continue
                if ch == '\'':
                    in_c = False
                i += 1; continue
            if ch == '"':
                in_s = True; i += 1; continue
            if ch == '\'':
                in_c = True; i += 1; continue
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    if end < L and s[end] == ';':
                        end += 1
                    return s[start:end].strip()
            i += 1
    return None

def strip_java_guard(s: str) -> str:
    s = s or ""
    # 移除 <think>...</think> 标签及其内容 (处理 R1 模型的思考过程)
    # 使用 re.DOTALL 确保 . 匹配换行符
    s = re.sub(r'<think>.*?</think>', '', s, flags=re.DOTALL)
    
    s = re.sub(r'```(?:\w+)?\s*', '', s)
    s = re.sub(r'\s*```', '', s)

    first = _extract_first_assert(s)
    if first:
        return first

    # 回退：取最后一个 assert（无需复杂括号匹配）
    last = None
    for m in re.finditer(r'(assertTrue|assert|assertEquals|assertNotNull|assertFalse|assertSame|assertNotSame)\s*\([^)]*\);?', s, re.DOTALL):
        last = m.group(0).strip()
    if last:
        if not last.endswith(';'):
            last += ';'
        return last
    return s
def is_relevant_to_prefix(bug_type: str, prefix: str, assertion: str) -> bool:
    """
    根据测试前缀(prefix)和断言(assertion)筛选变异体。
    这是一个启发式筛选，旨在过滤掉与当前测试场景明显不相关的变异类型。
    
    Args:
        bug_type: 植入的Bug类型 (如 "Null Reference Failures")
        prefix: 测试前缀代码
        assertion: 测试断言 (如 "exception" 或具体 assert 语句)
        
    Returns:
        bool: True 表示该变异体与测试相关，应该保留; False 表示应该丢弃
    """
    if not prefix:
        return True
        
    prefix_lower = prefix.lower()
    
    # 1. 异常测试场景：如果测试本身就是为了捕获异常，那么大多数变异都是相关的
    # 尤其是那些抛出异常或改变异常处理流程的变异
    if assertion == 'exception' or "catch" in prefix_lower or "expect" in prefix_lower:
        return True

    # 2. 针对特定 Bug 类型的启发式规则
    
    # Index Boundary Failures: 只有当测试涉及数组、集合索引操作时才有意义
    if "Index Boundary" in bug_type:
        keywords = ["array", "list", "map", "set", "[", "get(", "size", "length", "add(", "remove("]
        if not any(k in prefix_lower for k in keywords):
            # 如果测试代码完全没有集合操作特征，丢弃边界类 bug
            return False
            
    # String Processing Failures: 需要测试包含字符串操作
    if "String Processing" in bug_type:
        keywords = ["string", "subs", "index", "append", "format", "\"", "concat", "replace", "char"]
        if not any(k in prefix_lower for k in keywords):
            return False
            
    # Null Reference Failures: 通常只要有对象操作就相关，很难完全排除
    # 但如果测试全是基本类型计算 (int, boolean)，可能就不太相关
    if "Null Reference" in bug_type:
        # 如果没有任何 new 对象或 null 关键字，且看起来像纯数学计算
        if "new " not in prefix_lower and "null" not in prefix_lower:
            # 检查是否只有基本类型
            # 这是一个弱检测，避免误杀
            pass
            
    return True
