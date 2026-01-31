import os
import re
import json
import subprocess
from pathlib import Path
import time
import traceback
import javalang
from typing import Optional, Dict, List, Union
_ast_parse_cache: Dict[str, tuple] = {}
def _parse_file_cached(path: str):
    """返回 (tree, src) 并缓存以避免重复 parse"""
    if path in _ast_parse_cache:
        return _ast_parse_cache[path]
    try:
        with open(path, 'r', encoding='utf-8') as f:
            src = f.read()
        tree = javalang.parse.parse(src)
        _ast_parse_cache[path] = (tree, src)
        return tree, src
    except Exception as e:
        # 解析失败不抛，以便上层回退
        _ast_parse_cache[path] = (None, None)
        return None, None

def _norm_type_name(t: str) -> str:
    if not t:
        return ''
    s = re.sub(r'<.*?>', '', str(t))    # remove generics
    s = s.replace('[]', '')             # remove arrays
    s = s.split('.')[-1]                # simple name
    return s.strip()
def locate_java_file(class_name: str, main_path: str) -> Optional[str]:
    """
    更健壮地定位Java文件路径：
    - 支持内部类名（$）与带包名的类名；
    - 若 main_path 是具体文件则取其目录；
    - 若直拼失败，会在 main_path 及向上若干层做递归查找。
    """
    search_class_name = class_name.split('$')[0]
    simple_name = search_class_name.split('.')[-1]
    file_name = simple_name + ".java"

    # 如果传进来的是文件路径，使用其目录
    if os.path.isfile(main_path):
        main_dir = os.path.dirname(main_path)
    else:
        main_dir = main_path

    # 如果 class_name 带包名，优先尝试 main_dir + package_path + file_name
    if '.' in search_class_name:
        pkg_parts = search_class_name.split('.')[:-1]
        pkg_path = os.path.join(*pkg_parts) if pkg_parts else ""
        candidate = os.path.join(main_dir, pkg_path, file_name) if pkg_path else os.path.join(main_dir, file_name)
        if os.path.exists(candidate):
            return candidate

    # 直接在 main_dir 下查找 simple 文件名
    direct = os.path.join(main_dir, file_name)
    if os.path.exists(direct):
        return direct

    # 在 main_dir 下递归查找
    for root, dirs, files in os.walk(main_dir):
        if file_name in files:
            return os.path.join(root, file_name)

    # 向上最多三层目录，扩展搜索（处理 main_path 已包含 package 的情况）
    parent = main_dir
    for _ in range(3):
        parent = os.path.dirname(parent)
        if not parent or parent == '/' or parent == '':
            break
        for root, dirs, files in os.walk(parent):
            if file_name in files:
                return os.path.join(root, file_name)

    return None

class JavaMethodExtractor:
    """基于 AST 的 Java 方法提取器"""
    @staticmethod
    def _count_effective_lines(code: str) -> int:
        """
        统计代码的有效行数：去掉空行和仅包含单个大括号的行。
        """
        lines = code.splitlines()
        cnt = 0
        for ln in lines:
            s = ln.strip()
            if not s:
                continue
            if s in ('{', '}', '{}'):
                continue
            cnt += 1
        return cnt
    @staticmethod
    def extract_method_by_ast(file_path: str, method_name: str, param_types: list[str] | None = None, min_lines: int = 5) -> Optional[str]:
        """
        使用 AST 提取完整方法（支持按参数类型精确匹配）
        如果方法有效代码行数小于 min_lines，则返回 None。
        """
        try:
            tree, java_code = _parse_file_cached(file_path)
            if tree is None:
                return None
            candidates = []
            for path, node in tree.filter(javalang.tree.MethodDeclaration):
                if node.name == method_name:
                    candidates.append(node)

            if not candidates:
                return None

            def _check_and_return(node):
                code = JavaMethodExtractor._extract_method_source(java_code, node)
                if not code:
                    return None
                if JavaMethodExtractor._count_effective_lines(code) < min_lines:
                    return None
                return code

            if param_types:
                for node in candidates:
                    params = getattr(node, "parameters", []) or []
                    types = [_norm_type_name(getattr(p.type, "name", None) or str(getattr(p, "type", None))) for p in params]   
                    if len(types) != len(param_types):
                        continue
                    ok = True
                    for t, pt in zip(types, param_types):
                        # None in param_types means unknown on test side => wildcard
                        if pt is None:
                            continue
                        # compare normalized simple names
                        if not t:
                            ok = False
                            break
                        if _norm_type_name(t) != _norm_type_name(pt):
                            ok = False
                            break
                    if ok:
                        return _check_and_return(node)

            if len(candidates) > 1:
                counts: Dict[int, List] = {}
                for node in candidates:
                    cnt = len(getattr(node, "parameters", []) or [])
                    counts.setdefault(cnt, []).append(node)
                for cnt, nodes in counts.items():
                    if len(nodes) == 1:
                        return _check_and_return(nodes[0])
                return _check_and_return(candidates[0])
            else:
                return _check_and_return(candidates[0])

        except javalang.parser.JavaSyntaxError:
            return None
        except Exception:
            return None

    @staticmethod
    def _extract_method_source(source_code: str, method_node) -> str:
        lines = source_code.split('\n')
        # 获取方法起始位置
        start_line = method_node.position.line - 1
        # 向前查找注解和修饰符（actual_start 保留到可能的注解行）
        actual_start = start_line
        for i in range(start_line - 1, -1, -1):
            line = lines[i].strip()
            if line.startswith('@') or not line or line.startswith('//') or line.startswith('/*'):
                actual_start = i
            else:
                break
        # 查找方法结束位置（通过大括号匹配），维护跨行块注释状态
        brace_count = 0
        found_opening = False
        end_line = start_line
        in_comment = False
        for i in range(start_line, len(lines)):
            line = lines[i]
            # _remove_strings_and_comments 返回 (cleaned_line, in_comment_after)
            cleaned_line, in_comment = JavaMethodExtractor._remove_strings_and_comments(line, in_comment)
            for char in cleaned_line:
                if char == '{':
                    brace_count += 1
                    found_opening = True
                elif char == '}':
                    brace_count -= 1
                if found_opening and brace_count == 0:
                    end_line = i
                    method_text = '\n'.join(lines[actual_start:end_line + 1])
                    return JavaMethodExtractor._remove_comments_and_annotations(method_text)
        # 如果是抽象方法或接口方法（没有方法体）
        for i in range(start_line, min(start_line + 10, len(lines))):
            if ';' in lines[i]:
                method_text = '\n'.join(lines[actual_start:i + 1])
                return JavaMethodExtractor._remove_comments_and_annotations(method_text)
        method_text = '\n'.join(lines[actual_start:end_line + 1])
        return JavaMethodExtractor._remove_comments_and_annotations(method_text)
    @staticmethod
    def _extract_method_source_raw(source_code: str, method_node) -> str:
        lines = source_code.split('\n')
        start_line = method_node.position.line - 1
        actual_start = start_line
        for i in range(start_line - 1, -1, -1):
            line = lines[i].strip()
            if line.startswith('@') or not line or line.startswith('//') or line.startswith('/*'):
                actual_start = i
            else:
                break
        actual_start = start_line
        # 向上扩展以包含整个注解 / Javadoc / 注释 块
        for i in range(start_line - 1, -1, -1):
            raw = lines[i]
            line = raw.strip()
            # 包含：空行、注解行、行注释、块注释起始/结束、Javadoc 中以 '*' 开头的行
            if (not line
                or line.startswith('@')
                or line.startswith('//')
                or line.startswith('/*')
                or line.startswith('/**')
                or line.startswith('*/')
                or line.startswith('*')):
                actual_start = i
                continue
            # 其他情况中断（遇到非注释/注解的正常代码行）
            break
        brace_count = 0
        found_opening = False
        end_line = start_line
        in_comment = False
        for i in range(start_line, len(lines)):
            line = lines[i]
            # _remove_strings_and_comments 返回 (cleaned_line, in_comment_after)
            cleaned_line, in_comment = JavaMethodExtractor._remove_strings_and_comments(line, in_comment)
            for ch in cleaned_line:
                if ch == '{':
                    brace_count += 1
                    found_opening = True
                elif ch == '}':
                    brace_count -= 1
                if found_opening and brace_count == 0:
                    end_line = i
                    method_text = '\n'.join(lines[actual_start:end_line + 1])
                    return method_text
        # 抽象/接口方法回退
        for i in range(start_line, min(start_line + 10, len(lines))):
            if ';' in lines[i]:
                method_text = '\n'.join(lines[actual_start:i + 1])
                return method_text
        method_text = '\n'.join(lines[actual_start:end_line + 1])
        return method_text
    
    @staticmethod
    def _remove_strings_and_comments(line: str, in_comment: bool = False) -> tuple[str, bool]:
        """
        去除字符串/注释。接受并返回 in_comment 状态以支持跨行块注释。
        返回 (cleaned_line, in_comment_after).
        """
        in_string = False
        in_char = False
        result = []
        i = 0
        L = len(line)

        while i < L:
            # 若当前在块注释中，查找结束标记
            if in_comment:
                if i + 1 < L and line[i] == '*' and line[i+1] == '/':
                    in_comment = False
                    i += 2
                    continue
                i += 1
                continue

            # 行注释
            if i + 1 < L and line[i] == '/' and line[i+1] == '/':
                break

            # 块注释开始
            if i + 1 < L and line[i] == '/' and line[i+1] == '*':
                in_comment = True
                i += 2
                continue

            ch = line[i]
            # 字符串/字符字面量处理
            if ch == '"' and (i == 0 or line[i-1] != '\\'):
                in_string = not in_string
                # 不把字符串内容加入结果
                i += 1
                # skip until closing quote or end
                while i < L:
                    if line[i] == '"' and line[i-1] != '\\':
                        i += 1
                        break
                    i += 1
                continue
            if ch == "'" and (i == 0 or line[i-1] != '\\'):
                in_char = not in_char
                i += 1
                while i < L:
                    if line[i] == "'" and line[i-1] != '\\':
                        i += 1
                        break
                    i += 1
                continue

            # 非字符串/注释字符保留
            result.append(ch)
            i += 1

        return (''.join(result), in_comment)
    
    @staticmethod
    def _remove_comments_and_annotations(code: str) -> str:
        """
        移除给定代码片段中的注解和注释，尽量保留行/缩进结构，仅返回方法签名与方法体内容。
        算法：基于字符扫描，识别字符串/字符常量，跳过注释（行/块），并移除注解（独占行和内联注解）。
        """
        n = len(code)
        i = 0
        out = []
        in_string = False
        in_char = False

        def is_line_start_whitespace(buf):
            # 判断当前位置前是否到行首只包含空白
            j = len(buf) - 1
            while j >= 0 and buf[j] != '\n':
                if buf[j] not in (' ', '\t'):
                    return False
                j -= 1
            return True

        while i < n:
            ch = code[i]
            # 字符串与字符字面量处理
            if ch == '"' and not in_char and (i == 0 or code[i-1] != '\\'):
                in_string = not in_string
                out.append(ch)
                i += 1
                continue
            if ch == "'" and not in_string and (i == 0 or code[i-1] != '\\'):
                in_char = not in_char
                out.append(ch)
                i += 1
                continue

            if in_string or in_char:
                out.append(ch)
                i += 1
                continue

            # 行注释
            if ch == '/' and i + 1 < n and code[i+1] == '/':
                # skip until newline, but emit newline to preserve structure
                i += 2
                while i < n and code[i] != '\n':
                    i += 1
                # keep the newline if present
                if i < n and code[i] == '\n':
                    out.append('\n')
                    i += 1
                continue

            # 块注释
            if ch == '/' and i + 1 < n and code[i+1] == '*':
                # count newlines inside comment to preserve vertical spacing
                i += 2
                nl_count = 0
                while i < n:
                    if code[i] == '\n':
                        nl_count += 1
                    if code[i] == '*' and i + 1 < n and code[i+1] == '/':
                        i += 2
                        break
                    i += 1
                # emit same number of newlines to roughly preserve layout
                out.append('\n' * nl_count)
                continue

            # 注解处理：遇到 @ 时，判断是否为独占行（前面到行首仅空白）
            if ch == '@':
                buf = ''.join(out)
                line_start_ws = is_line_start_whitespace(buf)
                # skip annotation: consume identifier parts, dots and balanced parentheses
                j = i + 1
                # skip whitespace after @
                while j < n and code[j] in (' ', '\t'):
                    j += 1
                # consume annotation name and dots
                while j < n and re.match(r'[\w$\.]', code[j]):
                    j += 1
                # if parameter list
                if j < n and code[j] == '(':
                    depth = 0
                    while j < n:
                        if code[j] == '(':
                            depth += 1
                        elif code[j] == ')':
                            depth -= 1
                            if depth == 0:
                                j += 1
                                break
                        # handle string/char inside annotation params
                        if code[j] == '"' :
                            # skip string literal in params
                            j += 1
                            while j < n and not (code[j] == '"' and code[j-1] != '\\'):
                                j += 1
                            j += 1
                            continue
                        if code[j] == "'" :
                            j += 1
                            while j < n and not (code[j] == "'" and code[j-1] != '\\'):
                                j += 1
                            j += 1
                            continue
                        j += 1
                # advance j to end of annotation token (stop at newline or whitespace followed by non identifier/dot)
                while j < n and code[j] in (' ', '\t'):
                    j += 1
                # if annotation is standalone on the line, skip to newline and keep newline
                if line_start_ws:
                    while j < n and code[j] != '\n':
                        j += 1
                    if j < n and code[j] == '\n':
                        out.append('\n')
                        j += 1
                    i = j
                    continue
                else:
                    # inline annotation: replace with single space to avoid token merge
                    out.append(' ')
                    i = j
                    continue

            # 默认保留字符
            out.append(ch)
            i += 1

        result = ''.join(out)

        # 最后再做两步清理：移除可能残留的注解形态和收尾空白
        # 删除仍独占整行的注解（冗余保险）
        result = re.sub(r'^[ \t]*@\w+(?:\.[\w]+)*(?:\s*\([^)]*\))?[ \t]*\r?$', '', result, flags=re.MULTILINE)
        # 删除多余空行（最多保留两个连续空行）
        result = re.sub(r'\n{3,}', '\n\n', result)
        # 去除每行尾部空白
        result = '\n'.join(line.rstrip() for line in result.splitlines())
        return result.strip('\n')

    @staticmethod
    def extract_all_methods(file_path: str, min_lines: int = 0) -> List[Dict]:
        """
        提取文件中的所有方法
        返回字段：name, return_type, parameters([{name,type}]), modifiers, code, line_number
        仅返回有效代码行数 >= min_lines 的方法。
        """
        methods: List[Dict] = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                java_code = f.read()
            tree = javalang.parse.parse(java_code)
            for path, node in tree.filter(javalang.tree.MethodDeclaration):
                params = []
                for param in node.parameters:
                    params.append({
                        'name': param.name,
                        'type': str(param.type)
                    })
                code = JavaMethodExtractor._extract_method_source(java_code, node)
                raw_code = JavaMethodExtractor._extract_method_source_raw(java_code, node)
                if not code:
                    continue
                if JavaMethodExtractor._count_effective_lines(code) < min_lines:
                    continue
                method_info = {
                    'name': node.name,
                    'return_type': str(node.return_type) if node.return_type else 'void',
                    'parameters': params,
                    'modifiers': list(node.modifiers) if node.modifiers else [],
                    'code': code,
                    'raw_code': raw_code,
                    'line_number': node.position.line if node.position else -1
                }
                methods.append(method_info)
            return methods
        except Exception:
            return []
    @staticmethod
    def _infer_arg_type_from_invocation(arg_node, method_body_text: str = '') -> Optional[str]:
        """
        更智能的参数类型推断：支持变量回溯、对象创建、强制转换等多种形式。
        """
        if arg_node is None:
            return None
        
        # 字面量
        if isinstance(arg_node, javalang.tree.Literal):
            v = getattr(arg_node, 'value', '')
            if isinstance(v, str):
                if v.startswith('"') and v.endswith('"'):
                    return 'String'
                elif v == 'null':
                    return None
                elif v in ('true', 'false'):
                    return 'boolean'
            try:
                if re.match(r"^-?\d+L?$", str(v)):
                    return 'long' if str(v).endswith('L') else 'int'
                elif re.match(r"^-?\d+\.\d+[dDfF]?$", str(v)):
                    return 'double' if str(v).endswith(('d', 'D')) else 'float'
            except:
                pass
            return None
        
        # 强制转换
        if isinstance(arg_node, javalang.tree.Cast):
            cast_type = getattr(arg_node.type, 'name', None)
            return _norm_type_name(cast_type) if cast_type else None
        
        # 对象创建：new ClassName(...)
        if isinstance(arg_node, javalang.tree.ClassCreator):
            type_name = getattr(arg_node.type, 'name', None)
            return _norm_type_name(type_name) if type_name else None
        
        # 数组创建：new int[] {...}
        if isinstance(arg_node, javalang.tree.ArrayCreator):
            elem_type = getattr(arg_node.type, 'name', None)
            return (_norm_type_name(elem_type) + '[]') if elem_type else 'Object[]'
        
        # 变量/字段引用
        if isinstance(arg_node, javalang.tree.Name):
            var_name = getattr(arg_node, 'value', None)
            # 可以在这里添加变量声明回溯逻辑
            # 简单版本：返回None，让调用者做进一步处理
            return None
        
        # 方法调用返回值（递归）
        if isinstance(arg_node, javalang.tree.MethodInvocation):
            method_name = getattr(arg_node, 'member', None)
            # 某些常见方法的已知返回类型
            known_returns = {
                'toString': 'String',
                'length': 'int',
                'size': 'int',
                'isEmpty': 'boolean',
                'get': None,  # 依赖泛型
                'getValue': None,
            }
            return known_returns.get(method_name, None)
        
        # 三元操作符
        if isinstance(arg_node, javalang.tree.TernaryExpression):
            # 推断 true_expr 的类型（假设 true_expr 和 false_expr 类型一致）
            true_type = JavaMethodExtractor._infer_arg_type_from_invocation(
                getattr(arg_node, 'true_expression', None), method_body_text
            )
            return true_type
        
        return None
def find_method_implementation(class_name: str,
                               main_path: str,
                               method_name: str,
                               param_types: list[str] | None = None,
                               assert_meta: dict | None = None,
                               min_lines: int = 0) -> Optional[Union[Dict, List[Dict]]]:
    """
    定位 Java 文件并使用 AST 提取指定方法实现。
    优先精确匹配（按参数类型），失败后按参数个数/模糊匹配，若模糊匹配到多个则返回所有候选。
    """
    # 规范化 param_types：若全是 None 则视为未知
    if param_types is not None and all(t is None for t in param_types):
        param_types = None

    inferred_arg_count = len(param_types) if param_types is not None else None
    java_file_path = locate_java_file(class_name, main_path)
    if not java_file_path:
        return None

    tree, src = _parse_file_cached(java_file_path)
    if tree is None:
        return JavaMethodExtractor.extract_method_by_ast(java_file_path, method_name, param_types, min_lines=min_lines)

    candidates = [n for _, n in tree.filter(javalang.tree.MethodDeclaration)
                  if n.name == method_name and getattr(n, "position", None)]

    if not candidates:
        return JavaMethodExtractor.extract_method_by_ast(java_file_path, method_name, param_types, min_lines=min_lines)

    # 按提示的参数个数先过滤
    hint_arg_count = assert_meta.get('arg_count') if assert_meta else None
    desired_count = hint_arg_count if isinstance(hint_arg_count, int) else inferred_arg_count
    if isinstance(desired_count, int):
        by_count = [n for n in candidates if len(getattr(n, "parameters", []) or []) == desired_count]
        if by_count:
            candidates = by_count
        else:
            fuzzy = True  # 无匹配，转为模糊，不早退

    precise = False
    fuzzy = fuzzy if 'fuzzy' in locals() else False

    # 精确匹配
    if param_types is not None:
        def match_strict(n):
            # ...existing code...
            return True

        strict_list = [n for n in candidates if match_strict(n)]
        if strict_list:
            candidates = strict_list
            precise = True
        else:
            lenient_list = [n for n in candidates if len(getattr(n, "parameters", []) or []) == len(param_types)]
            if lenient_list:
                candidates = lenient_list
                fuzzy = True
            else:
                fuzzy = True  # 不早退，转模糊
    else:
        if len(candidates) > 1:
            fuzzy = True

    def to_result(node):
        code = JavaMethodExtractor._extract_method_source(src, node)
        raw = JavaMethodExtractor._extract_method_source_raw(src, node)
        if not code:
            return None
        if JavaMethodExtractor._count_effective_lines(code) < min_lines:
            return None
        return {'code': code, 'raw_code': raw, 'line': node.position.line if node.position else None}

    # 如果是模糊匹配且有多个候选，返回所有候选
    if fuzzy and len(candidates) > 1:
        results = []
        for n in candidates:
            r = to_result(n)
            if r:
                results.append(r)
        return results if results else None

    # 单一候选（或精确匹配选择最佳）
    chosen = candidates[0]

    # 若有行号提示，按距离排序选择
    if assert_meta and isinstance(assert_meta.get('line'), int):
        hint_line = int(assert_meta['line'])
        candidates.sort(key=lambda n: abs((n.position.line if n.position else hint_line) - hint_line))
        chosen = candidates[0]

    res = to_result(chosen)
    return res

def find_tested_methods_from_test_calls(test_file_path: str) -> List[Dict]:
    """
    使用 javalang 更精确地解析测试文件，识别在每个测试方法中，被调用的目标方法。
    返回每个被测方法的信息字典列表，字段包括：
      - class: 推断的被测类名（来自测试文件名）
      - method: 被调用的方法名
      - line_numbers: 调用所在行号列表（可能多个位置）
      - line_contents: 调用所在行文本列表（与 line_numbers 一一对应）
      - test_method: 包含该调用的测试方法名
      - object: 调用的对象/限定符（尽量提取）
      - prefixes: prefix 列表（每个调用对应完整的测试方法源码）
    若 javalang 解析失败则回退到基于正则的启发式实现。
    """
    tested_methods: List[Dict] = []
    try:
        with open(test_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading test file {test_file_path}: {e}")
        return []
    # 清理 EvoSuite 注解
    try:
        modified = False
        runwith_re = re.compile(r'@RunWith\s*\(\s*EvoSuiteRunner\.class\s*\)\s*', flags=re.MULTILINE)
        if runwith_re.search(content):
            content = runwith_re.sub('', content); modified = True
        import_evo_re = re.compile(r'^\s*import\s+org\.evosuite\.junit\.EvoSuiteRunner\s*;\s*\n', flags=re.MULTILINE)
        if import_evo_re.search(content):
            content = import_evo_re.sub('', content); modified = True
        import_runwith_re = re.compile(r'^\s*import\s+org\.junit\.runner\.RunWith\s*;\s*\n', flags=re.MULTILINE)
        if import_runwith_re.search(content):
            content = import_runwith_re.sub('', content); modified = True
        if modified:
            with open(test_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
    except Exception:
        pass

    test_file_name = os.path.basename(test_file_path)
    main_class_name = test_file_name[:-17] if test_file_name.endswith("EvoSuiteTest.java") else test_file_name.replace(".java", "")

    try:
        tree = javalang.parse.parse(content)
    except Exception:
        # 回退：逐调用返回，不合并
        try:
            lines = content.splitlines()
            i = 0
            naive_results = []
            while i < len(lines):
                line = lines[i]
                test_method_match = re.search(r'public\s+void\s+(test\w*)', line)
                if test_method_match:
                    current_test_method = test_method_match.group(1)
                    method_lines = []
                    j = i + 1
                    brace_count = line.count('{')
                    while j < len(lines):
                        current_line = lines[j]
                        method_lines.append(current_line)
                        brace_count += current_line.count('{') - current_line.count('}')
                        if brace_count == 0:
                            break
                        j += 1
                    all_lines = method_lines[1:]
                    for k, method_line in enumerate(all_lines):
                        method_call_pattern = r'(\w+)\s*\.\s*([a-zA-Z_]\w*)\s*\('
                        for match in re.finditer(method_call_pattern, method_line):
                            object_name = match.group(1)
                            method_name = match.group(2)
                            ignored_methods = {}
                            if method_name in ignored_methods:
                                continue
                            complete_prefix = ''.join(all_lines[:k+1]).strip()
                            method_call_line_num = i + 2 + k
                            naive_results.append({
                                'class': main_class_name,
                                'method': method_name,
                                'line_numbers': [method_call_line_num],
                                'line_contents': [method_line.strip()],
                                'test_method': current_test_method,
                                'object': object_name,
                                'prefixes': [complete_prefix],
                                'arg_types': None,
                                'arg_count': None
                            })
                i += 1
            return naive_results
        except Exception:
            traceback.print_exc()
            return []

    try:
        lines_full = content.splitlines(True)

        def idx_from_pos(line: int, column: int) -> int:
            if line is None or column is None or line <= 0 or line > len(lines_full):
                return 0
            return sum(len(lines_full[i]) for i in range(line - 1)) + max(0, column - 1)

        def _count_call_args(src: str, start_idx: int) -> int:
            n = len(src)
            # 找到第一个 '('（可能存在空白或类型参数 <...>）
            i = start_idx
            # 跳过标识符/泛型/空白
            while i < n and src[i] not in ('(', ';', '\n'):
                # 跳过尖括号内的类型参数
                if src[i] == '<':
                    depth = 1
                    i += 1
                    while i < n and depth > 0:
                        if src[i] == '<':
                            depth += 1
                        elif src[i] == '>':
                            depth -= 1
                        # 跳过字符串/字符
                        if src[i] == '"':
                            i += 1
                            while i < n and not (src[i] == '"' and src[i-1] != '\\'):
                                i += 1
                        elif src[i] == "'":
                            i += 1
                            while i < n and not (src[i] == "'" and src[i-1] != '\\'):
                                i += 1
                        i += 1
                    continue
                i += 1
            if i >= n or src[i] != '(':
                return 0
            # 统计顶层逗号
            i += 1
            depth = 1
            in_block = False
            count_commas = 0
            had_token = False
            while i < n and depth > 0:
                ch = src[i]
                # 块注释
                if not in_block and ch == '/' and i+1 < n and src[i+1] == '*':
                    in_block = True
                    i += 2
                    continue
                if in_block:
                    if ch == '*' and i+1 < n and src[i+1] == '/':
                        in_block = False
                        i += 2
                        continue
                    i += 1
                    continue
                # 行注释
                if ch == '/' and i+1 < n and src[i+1] == '/':
                    while i < n and src[i] != '\n':
                        i += 1
                    continue
                # 字符串/字符
                if ch == '"':
                    i += 1
                    while i < n and not (src[i] == '"' and src[i-1] != '\\'):
                        i += 1
                    i += 1
                    had_token = True
                    continue
                if ch == "'":
                    i += 1
                    while i < n and not (src[i] == "'" and src[i-1] != '\\'):
                        i += 1
                    i += 1
                    had_token = True
                    continue
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0:
                        break
                elif ch == ',' and depth == 1:
                    count_commas += 1
                    had_token = False
                else:
                    # 非空白视为存在参数 token
                    if ch not in (' ', '\t', '\n', '\r'):
                        had_token = True
                i += 1
            # 参数个数 = 顶层逗号数 +（是否存在最后一个参数）
            return (count_commas + (1 if had_token else 0))
        
        ignored_methods = {}

        tested_methods = []  # 不合并，逐调用返回

        for path, method in tree.filter(javalang.tree.MethodDeclaration):
            is_test = method.name.startswith('test') if method.name else False
            if not is_test and getattr(method, 'annotations', None):
                for ann in method.annotations:
                    ann_name = getattr(ann.name, 'value', ann.name) if ann is not None else None
                    if ann_name and (ann_name == 'Test' or ann_name.endswith('.Test')):
                        is_test = True
                        break
            if not is_test:
                continue

            invocations = []
            for _, inv in method.filter(javalang.tree.MethodInvocation):
                member = getattr(inv, 'member', None)
                qual = getattr(inv, 'qualifier', None)
                pos = getattr(inv, 'position', None)
                if not member or not pos:
                    continue

                # 先按 AST 推断类型
                arg_types: List[Optional[str]] = []
                try:
                    # javalang 可能返回 Argument 包装，统一解包 expression
                    raw_args = getattr(inv, 'arguments', []) or []
                    norm_args = []
                    for a in raw_args:
                        expr = getattr(a, 'expression', None)
                        norm_args.append(expr if expr is not None else a)
                    for a in norm_args:
                        inferred = JavaMethodExtractor._infer_arg_type_from_invocation(a, '')
                        arg_types.append(inferred)
                except Exception:
                    arg_types = []

                # 若 AST 无法获取参数，使用源码计数回退，并填充类型为 None
                if len(arg_types) == 0:
                    call_idx = idx_from_pos(pos.line, pos.column)
                    fallback_count = _count_call_args(content, call_idx)
                    if fallback_count > 0:
                        arg_types = [None] * fallback_count

                invocations.append({
                    'member': member,
                    'qualifier': qual,
                    'pos': pos,
                    'index': idx_from_pos(pos.line, pos.column),
                    'arg_count': len(arg_types),
                    'arg_types': arg_types
                })
            invocations.sort(key=lambda x: x['index'])
            all_invocations = invocations

            for inv in all_invocations:
                method_name = inv['member']
                if method_name in ignored_methods:
                    continue
                object_name = inv['qualifier'] if inv['qualifier'] is not None else ''
                object_type = ''
                line_no = inv['pos'].line
                line_text = lines_full[line_no - 1].strip() if 0 <= line_no - 1 < len(lines_full) else ''

                if getattr(method, 'position', None):
                    full_method_source = JavaMethodExtractor._extract_method_source_raw(content, method)
                    mlines = full_method_source.splitlines(keepends=True)
                    filtered = []
                    public_line_index = -1
                    for i, ln in enumerate(mlines):
                        s = ln.strip()
                        if any(s.startswith(mod) for mod in ['public', 'private', 'protected']):
                            public_line_index = i
                            break
                    if public_line_index != -1:
                        filtered = mlines[public_line_index:]
                    else:
                        filtered = mlines
                    final_lines = [ln for ln in filtered if not ln.strip().startswith('@')]
                    prefix_text_full = ''.join(final_lines).rstrip()
                else:
                    prefix_text_full = ''

                tested_methods.append({
                    'class': main_class_name,
                    'method': method_name,
                    'line_numbers': [inv['pos'].line],
                    'line_contents': [line_text],
                    'test_method': method.name,
                    'object': object_name,
                    'object_type': object_type,
                    'prefixes': [prefix_text_full],
                    'arg_types': inv.get('arg_types'),
                    'arg_count': inv.get('arg_count')
                })

        return tested_methods

    except Exception as e:
        print(f"Error parsing test file {test_file_path} with javalang: {e}")
        traceback.print_exc()
        return []
    
def traverse_fixed_projects(root_dir: str) -> List[Dict]:
    results: List[Dict] = []
    project_dirs = [d for d in os.listdir(root_dir)
                    if os.path.isdir(os.path.join(root_dir, d))]
    idx = 0
    for project_dir in project_dirs:
        result: Dict = {}
        project_path = os.path.join(root_dir, project_dir)
        src_paths = [
            os.path.join(project_path, 'evosuite-tests'),
        ]

        main_paths = [
            os.path.join(project_path, 'src', 'main', 'java'),
            os.path.join(project_path, 'src', 'java')
        ]
        parts = project_dir.split('_')
        # or parts[0]  in not_care
        if len(parts) != 2:
            continue
        print(parts)
        test_partten = []
        for i, src_path in enumerate(src_paths):
            main_path = main_paths[i] if i < len(main_paths) else (main_paths[0] if main_paths else None)
            if not main_path:
                continue
            if os.path.exists(src_path):
                for root, dirs, files in os.walk(src_path):
                    for file in files:
                        if file.endswith('EvoSuiteTest.java'):
                            # 计算相对于 src_path 的相对路径，使用 pathlib 保持跨平台鲁棒性
                            rel = os.path.relpath(os.path.join(root, file), src_path)
                            rel_parts = Path(rel).parts  # 包含 package path + filename
                            test_class = Path(file).stem.replace('EvoSuiteTest', '')
                            pkg_parts = rel_parts[:-1]   # package segments (可能为空)
                            # 构造 test_name（如 com.ib.client.TickType）
                            if pkg_parts:
                                test_name = '.'.join((*pkg_parts, test_class))
                            else:
                                test_name = test_class

                            # 方法路径（用于定位测试文件）和实际测试文件路径
                            method_path = os.path.join(*pkg_parts) if pkg_parts else ''
                            actual_test_file_path = os.path.join(root, file)  # 更直接、可靠

                            # 构造对应的被测 .java 在主源码树上的预期位置（在 main_path 下加 package）
                            full_main_path = os.path.join(main_path, *pkg_parts, test_class + '.java')

                            # 回退策略：如果 full_main_path 不存在，尝试更宽松的 locate_java_file
                            if not os.path.exists(full_main_path):
                                # locate_java_file 已经实现向上搜索与 os.walk 回退，优先使用它
                                possible = locate_java_file(test_class, main_path)
                                if possible:
                                    full_main_path = possible

                            assert_method = find_tested_methods_from_test_calls(actual_test_file_path)
                            find_method = []
                            for item in assert_method:
                                # 提供额外提示以提高定位准确度：优先使用断言中记录的行号和 object（若有）
                                hint = {}
                                if item.get('line_numbers'):
                                    hint['line'] = item['line_numbers'][0]
                                if item.get('object'):
                                    hint['object'] = item.get('object')
                                if item.get('arg_count') is not None:
                                    hint['arg_count'] = item['arg_count']
                                param_types = item.get('arg_types') if item.get('arg_types') is not None else None
                                method = find_method_implementation(
                                    item['class'],
                                    full_main_path,
                                    item['method'],
                                    param_types=param_types,
                                    assert_meta=hint,
                                )
                                if method is None:
                                    continue
                                if isinstance(method, list):
                                    for m in method:
                                        new_item = item.copy()
                                        new_item["focal_method"] = m["code"]
                                        new_item["raw_method"] = m["raw_code"]
                                        new_item["candidate_line"] = m.get("line")
                                        find_method.append(new_item)
                                else:
                                    new_item = item.copy()
                                    new_item["focal_method"] = method["code"]
                                    new_item["raw_method"] = method["raw_code"]
                                    find_method.append(new_item)
                            test_partten.append({
                                "test_name": test_name,
                                "test_class": test_name.split('.')[-1],
                                "run_test_name": test_name + '_ESTest',
                                "method_path": actual_test_file_path,
                                "main_path": full_main_path,
                                "result": find_method
                            })
        result['id'] = idx
        idx += 1
        result['project'] = parts[1]
        result['bug_num'] = parts[0]
        result['test_partten'] = test_partten
        results.append(result)
    return results

def save_results_to_json(results, output_file: str):
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

_seen = set()
def main():
    root_directory = os.getcwd()
    if not os.path.exists(root_directory):
        print(f"Directory {root_directory} does not exist.")
        root_directory = input("Please enter the correct root directory path: ")
        if not os.path.exists(root_directory):
            print("Invalid directory. Exiting.")
            return
    print(f"Traversing fixed projects in {root_directory}...")
    results = traverse_fixed_projects(root_directory)
    output_file = "single_return_methods.json"
    save_results_to_json(results, output_file)
    print(f"Results saved to {output_file}")

if __name__ == "__main__":
    main()