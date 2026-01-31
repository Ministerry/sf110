import re
from inject import BugInject
import javalang
from javalang.tree import MethodDeclaration, TryStatement, CatchClause, MethodInvocation
from typing import List, Dict, Any,Optional,Set
import os
from inject import unparse
import random
java_template = """
public class Test {{
    {code}
}}
"""
def ast_get_statements(method_code: str) -> List[str]:
    """
    使用 javalang 解析 method_code（可为方法体或完整方法），返回结构化语句签名列表。
    签名示例: "if:x==null", "if:x instanceof Type", "assign:a=b", "invoke:obj.method", "return:expr"
    """
    src = java_template.format(code=method_code)
    try:
        cu = javalang.parse.parse(src)
    except Exception:
        # 解析失败退回简单行级提取
        lines = [l.strip() for l in method_code.splitlines() if l.strip()]
        return lines

    # find first MethodDeclaration
    method_node = None
    for t in getattr(cu, 'types', []) or []:
        for m in getattr(t, 'methods', []) or []:
            method_node = m
            break
        if method_node:
            break
    if method_node is None:
        return []


    stmts: List[str] = []

    def visit(node):
        if node is None:
            return
        name = type(node).__name__
        # IfStatement
        if name == 'IfStatement':
            cond = unparse(node.condition)
            stmts.append(f"if:{cond}")
            # then / else can be blocks or single statements
            visit(node.then_statement)
            visit(node.else_statement)
            return
        # ReturnStatement
        if name == 'ReturnStatement':
            stmts.append(f"return:{unparse(getattr(node, 'expression', None))}")
            return
        # LocalVariableDeclaration
        if name == 'LocalVariableDeclaration':
            parts = []
            typ = getattr(node, 'type', None)
            tname = getattr(typ, 'name', '') if typ else ''
            for decl in getattr(node, 'declarators', []) or []:
                parts.append(getattr(decl, 'name', ''))
            stmts.append(f"var:{tname}:{','.join(parts)}")
            return
        # StatementExpression (could be assignment or invocation)
        if name == 'StatementExpression':
            expr = getattr(node, 'expression', None)
            if expr is None:
                return
            et = type(expr).__name__
            if et == 'Assignment':
                left = unparse(expr.expressionl)
                right = unparse(expr.value)
                stmts.append(f"assign:{left}={right}")
            elif et == 'MethodInvocation':
                qual = getattr(expr, 'qualifier', None)
                mem = getattr(expr, 'member', '')
                stmts.append(f"invoke:{qual + '.' if qual else ''}{mem}")
            else:
                stmts.append(f"expr:{et}")
            return
        # Block (list of statements)
        if hasattr(node, 'statements'):
            for s in getattr(node, 'statements') or []:
                visit(s)
            return
        # For enhanced coverage, traverse attributes that may contain nodes
        for attr in dir(node):
            if attr.startswith('_'):
                continue
            try:
                val = getattr(node, attr)
            except Exception:
                continue
            if isinstance(val, list):
                for it in val:
                    if hasattr(it, '__class__') and it.__class__.__module__.startswith('javalang'):
                        visit(it)
            elif hasattr(val, '__class__') and val.__class__.__module__.startswith('javalang'):
                visit(val)

    visit(method_node.body)
    # normalize: strip spaces + lowercase for comparisons
    return [re.sub(r'\s+', '', s).lower() for s in stmts]
def is_path_based_variant_ast(original_code: str, variant_code: str, path_info: List[Any]) -> bool:
    """
    简单按行比对实现：
    - 将 original/variant 按行规范化后找出 removed/added 行
    - 将 path_info 中的每个路径元素也规范化
    - 若任何 removed 或 added 行出现在任一路径元素（相等或子串匹配）则返回 True，否则 False
    """
    def norm_line(line: str) -> str:
        if not line:
            return ''
        s = line.strip()
        # remove surrounding braces and trailing semicolons
        s = re.sub(r'^[\{\}]+|[\{\}]+$', '', s).strip()
        s = s.rstrip(';').strip()
        # normalize whitespace and case
        s = re.sub(r'\s+', ' ', s).strip().lower()
        # if it's an if(...) line, keep only the condition content for better match
        m = re.match(r'^\s*(?:else\s+if|if)\s*\((.*)\)\s*$', s, flags=re.I)
        if m:
            cond = m.group(1).strip()
            # remove outer parentheses from condition
            while cond.startswith('(') and cond.endswith(')'):
                cond = cond[1:-1].strip()
            cond = re.sub(r'\s+', ' ', cond).lower()
            # canonicalize null==x -> x==null
            m2 = re.match(r'^null\s*==\s*([a-z_][a-z0-9_\.]*)$', cond)
            if m2:
                return f"{m2.group(1)} == null"
            return cond
        return s

    orig_lines = [norm_line(l) for l in original_code.splitlines() if l.strip()]
    var_lines = [norm_line(l) for l in variant_code.splitlines() if l.strip()]

    set_orig = set(orig_lines)
    set_var = set(var_lines)
    removed = set_orig - set_var
    added = set_var - set_orig

    # build normalized path element list
    path_elems = []
    for p in path_info:
        seq = p['path'] if isinstance(p, dict) and 'path' in p else p
        for elem in seq:
            ne = norm_line(elem)
            if ne:
                path_elems.append(ne)

    # helper for match (exact or substring)
    def any_match(items: Set[str]) -> bool:
        if not items:
            return False
        for it in items:
            if not it:
                continue
            for pe in path_elems:
                if it == pe or it in pe or pe in it:
                    return True
        return False

    if any_match(removed) or any_match(added):
        return True
    return False
# -------------------------- 测试示例（使用你提供的参数） --------------------------
if __name__ == "__main__":
    # 原方法代码
    focal_method = """ public void doubleMouseClick (MouseEvent e)
        {
                if (e.getSource() != this)
                        return;

                Object o = getSelectedValue();
                if (null == o)
                        return;

                doubleClickEntry (o);
        }
        """

    # 执行路径
    path = [{'path': ['if (e.getSource() != this)', 'return;'], 'unparsed': 'if (e.getSource() != this) {\n  return;\n}'}, {'path': ['Object o = getSelectedValue();', 'if (null == o)', 'return;'], 'unparsed': 'Object o = getSelectedValue();\nif (null == o) {\n  return;\n}'}, {'path': ['Object o = getSelectedValue();', 'doubleClickEntry(o);'], 'unparsed': 'Object o = getSelectedValue();\ndoubleClickEntry(o);'}]

    # 变体方法代码
    variant_method = """
    public void doubleMouseClick(MouseEvent e) {
        if ((e.getSource() != this)) {
            return ;
        }
        Object o = getSelectedValue();
        doubleClickEntry(o);
    }
    """

    # 调用方法判断
    result = is_path_based_variant_ast(focal_method, variant_method, path)
    print(f"是否为路径植入的变体：{result}")  # 输出 True