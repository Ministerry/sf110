import javalang
import copy
import types
import copy
import javalang.tree as _jtree
from javalang.tree import (
    MethodDeclaration, BlockStatement, ReturnStatement, BinaryOperation, Literal, 
    FormalParameter, BasicType, IfStatement, LocalVariableDeclaration, WhileStatement,DoStatement,
    VariableDeclarator, StatementExpression, Assignment, ReferenceType, MemberReference,TryStatement,ArrayCreator,ForStatement,SynchronizedStatement
)
import json
import re
import copy
from javalang.ast import Node
from io import StringIO
def _safe_unparse_attr(obj):
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    return unparse(obj)
def _is_null_literal(node):
    # javalang Literal uses value attribute like 'null' or '"str"'
    try:
        return getattr(node, "value", None) == "null" or (isinstance(node, str) and node == "null")
    except Exception:
        return False
def _is_null_check(cond):
    """
    判断 cond 是否为 x != null 或 null != x 或 x == null（可扩展）
    返回 (is_check, var_name, op)  op in {'!=','=='}
    """
    if cond is None:
        return (False, None, None)
    t = type(cond).__name__
    if t == "BinaryOperation":
        op = getattr(cond, "operator", None)
        left = getattr(cond, "operandl", None)
        right = getattr(cond, "operandr", None)
        # left or right may be MemberReference or Literal
        if _is_null_literal(left) and hasattr(right, "member"):
            return (op in ("==", "!="), getattr(right, "member", None), op)
        if _is_null_literal(right) and hasattr(left, "member"):
            return (op in ("==", "!="), getattr(left, "member", None), op)
        # also accept simple identifiers (name attr)
        if _is_null_literal(left) and hasattr(right, "name"):
            return (op in ("==", "!="), getattr(right, "name", None), op)
        if _is_null_literal(right) and hasattr(left, "name"):
            return (op in ("==", "!="), getattr(left, "name", None), op)
    return (False, None, None)
def _unwrap_if_statement_in_parent(if_node, parent):
    """
    将 parent 中的 if_node 用其 then_statement 的内容替换（展开）。
    兼容多种 javalang AST 变体，并在展开时保留 else / else-if 语义：
    - 如果 if 有 else/else-if，则替换为: <then_statements>; if (!cond) { <else_stmt> }
    返回 True/False 表示是否成功替换。
    """
    then_stmt = getattr(if_node, "then_statement", None)
    if then_stmt is None:
        return False

    # normalize then_statements into a concrete list of statements to insert
    def _flatten_then(node):
        if node is None:
            return []
        # If it's already a Block-like with statements, use them
        try:
            if hasattr(node, "statements") and getattr(node, "statements") is not None:
                return list(getattr(node, "statements"))
        except Exception:
            pass
        # If it's a wrapper that holds the real statement in .statement, unwrap it
        try:
            inner = getattr(node, "statement", None)
            if inner is not None:
                # inner might itself be a block
                if hasattr(inner, "statements") and getattr(inner, "statements") is not None:
                    return list(getattr(inner, "statements"))
                return [inner]
        except Exception:
            pass
        # If it's an expression-statement wrapper, keep the node itself as a single statement
        try:
            if hasattr(node, "expression") and getattr(node, "expression") is not None:
                return [node]
        except Exception:
            pass
        # Fallback: treat node as a single statement
        return [node]

    stmts_to_insert = _flatten_then(then_stmt)

    # capture else to preserve semantics
    else_stmt = getattr(if_node, "else_statement", None)

    # prepare textual fallback for comparisons
    try:
        if_src = unparse(if_node)
    except Exception:
        if_src = None

    # helper to create an inverted-condition IfStatement that holds the else branch
    def _make_inverted_if(orig_cond, else_node):
        """
        Build a robust IfStatement with condition `!(orig_cond)` and then_statement = else_node.
        Tries to create proper javalang IfStatement/UnaryOperation; on any failure returns a
        lightweight IfStatement-like object that unparse() can still render.
        """
        try:
            if orig_cond is None or else_node is None:
                return None
            # deep-copy condition to avoid mutating original AST
            cond_copy = copy.deepcopy(orig_cond)

            # wrap in ParenthesizedExpression if available
            try:
                paren = _jtree.ParenthesizedExpression(expression=cond_copy)
            except Exception:
                paren = cond_copy

            # create unary not expression: !(cond)
            try:
                not_expr = _jtree.UnaryOperation(operator='!', expression=paren)
            except Exception:
                # fallback: try to build a simple Namespace-like unary node compatible with unparse
                try:
                    U = type("UnaryOperation", (), {})
                    not_expr = U()
                    not_expr.operator = '!'
                    not_expr.expression = paren
                except Exception:
                    not_expr = paren

            # ensure else_node is a statement-like: if it's a list/splice, wrap into Block-like
            else_stmt_node = None
            try:
                if type(else_node).__name__ == 'IfStatement':
                    else_stmt_node = copy.deepcopy(else_node)
                elif hasattr(else_node, "statements") and getattr(else_node, "statements") is not None:
                    else_stmt_node = copy.deepcopy(else_node)
                else:
                    B = type("Block", (), {})
                    b = B()
                    b.statements = [copy.deepcopy(else_node)]
                    else_stmt_node = b
            except Exception:
                else_stmt_node = copy.deepcopy(else_node)

            # build proper javalang.IfStatement if available
            try:
                inv_if = _jtree.IfStatement(condition=not_expr, then_statement=else_stmt_node, else_statement=None)
                return inv_if
            except Exception:
                # fall through to textual-safe fallback below
                pass

        except Exception:
            # outer try failed; will attempt fallback
            pass

        # Fallback: construct a lightweight IfStatement-like object with a Literal condition of the inverted text.
        try:
            cond_text = None
            try:
                cond_text = unparse(orig_cond)
            except Exception:
                cond_text = None
            if cond_text is None:
                cond_text = str(orig_cond) if orig_cond is not None else ""
            # create a Literal that carries the textual inverted condition "!(...)" so unparse prints it
            try:
                lit_cond = Literal(value="!(" + cond_text + ")")
            except Exception:
                # as absolute last resort, create a simple namespace carrying condition string
                lit_cond = types.SimpleNamespace()
                lit_cond.value = "!(" + cond_text + ")"
            # prepare else block wrapper
            try:
                if type(else_node).__name__ == 'IfStatement':
                    else_node_copy = copy.deepcopy(else_node)
                elif hasattr(else_node, "statements") and getattr(else_node, "statements") is not None:
                    else_node_copy = copy.deepcopy(else_node)
                else:
                    B = type("Block", (), {})
                    b = B()
                    b.statements = [copy.deepcopy(else_node)]
                    else_node_copy = b
            except Exception:
                else_node_copy = copy.deepcopy(else_node)
            # create dynamic IfStatement-like object
            IfStub = type("IfStatement", (), {})
            inst = IfStub()
            inst.condition = lit_cond
            inst.then_statement = else_node_copy
            inst.else_statement = None
            return inst
        except Exception:
            return None

    # helper: try replace in a list of statements (handles wrappers)
    def _replace_in_list(stmts):
        """
        Robust replacement of an if_node entry inside `stmts`.
        Adds DEBUG logging to help diagnose why then-statements may be lost.
        """
        DEBUG = False  # set True locally for verbose diagnostics

        try:
            lst = list(stmts) if stmts is not None else []
        except Exception:
            return False

        if DEBUG:
            try:
                print("DEBUG: _replace_in_list called, parent:", getattr(parent, "__class__", None), "orig_stmts_len=", len(lst))
            except Exception:
                pass

        for idx, s in enumerate(list(lst)):
            try:
                matched = False
                match_kind = None
                if s is if_node:
                    matched = True
                    match_kind = "identity"
                elif hasattr(s, "statement") and getattr(s, "statement") is if_node:
                    matched = True
                    match_kind = ".statement wrapper"
                elif hasattr(s, "expression") and getattr(s, "expression") is if_node:
                    matched = True
                    match_kind = ".expression wrapper"
                else:
                    # textual fallback
                    if if_src is not None:
                        try:
                            s_src = unparse(s)
                            if s_src == if_src or if_src in s_src or s_src in if_src:
                                matched = True
                                match_kind = "textual"
                        except Exception:
                            pass

                if not matched:
                    continue

                if DEBUG:
                    try:
                        print(f"DEBUG: matched if_node at idx={idx} kind={match_kind} -- building insert_seq")
                    except Exception:
                        pass

                # build insertion sequence as deep copies to avoid shared nodes
                insert_seq = []
                for it in stmts_to_insert:
                    try:
                        insert_seq.append(copy.deepcopy(it))
                    except Exception:
                        insert_seq.append(it)

                # append inverted-if for else preservation if present
                if else_stmt is not None:
                    inv_if = _make_inverted_if(getattr(if_node, "condition", None), else_stmt)
                    if inv_if is not None:
                        try:
                            insert_seq.append(copy.deepcopy(inv_if))
                        except Exception:
                            insert_seq.append(inv_if)

                if DEBUG:
                    try:
                        print("DEBUG: insert_seq count=", len(insert_seq))
                        for e in insert_seq:
                            try:
                                print("  ->", unparse(e).splitlines()[0])
                            except Exception:
                                pass
                    except Exception:
                        pass

                # perform splice on the local list
                lst[idx:idx+1] = insert_seq

                # write back to original container when necessary
                try:
                    # if original stmts is a real list object we can mutate it in-place
                    if isinstance(stmts, list):
                        stmts[:] = lst
                    else:
                        # common parent slot: parent.statements
                        if parent is not None:
                            try:
                                # if parent has 'statements' and it equals the original stmts, set it
                                if hasattr(parent, "statements") and getattr(parent, "statements") is stmts:
                                    setattr(parent, "statements", lst)
                                else:
                                    # search parent attrs to find the matching attribute and replace
                                    for a in getattr(parent, "attrs", []) or []:
                                        try:
                                            v = getattr(parent, a)
                                        except Exception:
                                            v = None
                                        if v is stmts:
                                            try:
                                                setattr(parent, a, lst)
                                                break
                                            except Exception:
                                                pass
                            except Exception:
                                # final fallback: attempt to set common single-slot holders
                                try:
                                    if hasattr(parent, "statement") and getattr(parent, "statement") is stmts:
                                        setattr(parent, "statement", lst[0] if len(lst) == 1 else types.SimpleNamespace(statements=lst))
                                except Exception:
                                    pass
                    if DEBUG:
                        try:
                            print("DEBUG: write-back done, new_len=", len(lst))
                        except Exception:
                            pass
                    return True
                except Exception:
                    if DEBUG:
                        print("DEBUG: write-back failed")
                    return False
            except Exception:
                continue
        return False

    # Case A: parent has statements list
    try:
        if hasattr(parent, "statements") and getattr(parent, "statements") is not None:
            if _replace_in_list(getattr(parent, "statements")):
                return True
    except Exception:
        pass

    # Case B: parent is a wrapper with .statement slot
    try:
        if hasattr(parent, "statement"):
            ps = getattr(parent, "statement")
            # parent.statement directly refers to the IfStatement
            if ps is if_node:
                # when replacing a single-slot, if there is an else we must make a Block-like sequence:
                seq = list(stmts_to_insert)
                if else_stmt is not None:
                    inv_if = _make_inverted_if(getattr(if_node, "condition", None), else_stmt)
                    if inv_if is not None:
                        seq.append(inv_if)
                if len(seq) == 1:
                    parent.statement = seq[0]
                else:
                    ns = types.SimpleNamespace()
                    ns.statements = seq
                    parent.statement = ns
                return True
            # or parent.statement is a Block-like containing statements
            try:
                if hasattr(ps, "statements") and getattr(ps, "statements") is not None:
                    if _replace_in_list(getattr(ps, "statements")):
                        return True
            except Exception:
                pass
            # textual fallback
            if if_src is not None:
                try:
                    ps_s = unparse(ps)
                    if ps_s == if_src or if_src in ps_s:
                        seq = list(stmts_to_insert)
                        if else_stmt is not None:
                            inv_if = _make_inverted_if(getattr(if_node, "condition", None), else_stmt)
                            if inv_if is not None:
                                seq.append(inv_if)
                        parent.statement = None if len(seq) == 0 else (seq[0] if len(seq) == 1 else types.SimpleNamespace(statements=seq))
                        return True
                except Exception:
                    pass
    except Exception:
        pass

    # Case C: parent.body / parent.block (MethodDeclaration / Try etc.)
    for attr in ("body", "block"):
        try:
            if hasattr(parent, attr) and getattr(parent, attr) is not None:
                container = getattr(parent, attr)
                # container may be Block-like or plain list
                if isinstance(container, (list, tuple)):
                    if _replace_in_list(container):
                        try:
                            setattr(parent, attr, list(container))
                        except Exception:
                            pass
                        return True
                else:
                    try:
                        if hasattr(container, "statements") and container.statements is not None:
                            if _replace_in_list(getattr(container, "statements")):
                                return True
                        # direct identity
                        if container is if_node:
                            # replace with sequence preserving else
                            seq = list(stmts_to_insert)
                            if else_stmt is not None:
                                inv_if = _make_inverted_if(getattr(if_node, "condition", None), else_stmt)
                                if inv_if is not None:
                                    seq.append(inv_if)
                            try:
                                if len(seq) == 0:
                                    setattr(parent, attr, None)
                                elif len(seq) == 1:
                                    setattr(parent, attr, seq[0])
                                else:
                                    ns = types.SimpleNamespace()
                                    ns.statements = seq
                                    setattr(parent, attr, ns)
                                return True
                            except Exception:
                                pass
                    except Exception:
                        pass
        except Exception:
            pass

    # Last resort: scan attributes listed in parent.attrs and do textual replace
    try:
        attrs = getattr(parent, "attrs", []) or []
        for a in attrs:
            try:
                v = getattr(parent, a)
            except Exception:
                v = None
            if v is None:
                continue
            if isinstance(v, (list, tuple)):
                if _replace_in_list(v):
                    try:
                        setattr(parent, a, list(v))
                    except Exception:
                        pass
                    return True
            else:
                if v is if_node:
                    try:
                        seq = list(stmts_to_insert)
                        if else_stmt is not None:
                            inv_if = _make_inverted_if(getattr(if_node, "condition", None), else_stmt)
                            if inv_if is not None:
                                seq.append(inv_if)
                        if len(seq) == 0:
                            setattr(parent, a, None)
                        elif len(seq) == 1:
                            setattr(parent, a, seq[0])
                        else:
                            setattr(parent, a, types.SimpleNamespace(statements=seq))
                        return True
                    except Exception:
                        pass
                if if_src is not None:
                    try:
                        if unparse(v) == if_src or if_src in unparse(v):
                            seq = list(stmts_to_insert)
                            if else_stmt is not None:
                                inv_if = _make_inverted_if(getattr(if_node, "condition", None), else_stmt)
                                if inv_if is not None:
                                    seq.append(inv_if)
                            try:
                                if len(seq) == 0:
                                    setattr(parent, a, None)
                                elif len(seq) == 1:
                                    setattr(parent, a, seq[0])
                                else:
                                    setattr(parent, a, types.SimpleNamespace(statements=seq))
                                return True
                            except Exception:
                                pass
                    except Exception:
                        pass
    except Exception:
        pass

    return False
def _is_var_used_after_in_method(method_node, path_of_if, if_node, var_name):
    """
    更宽松的 used-after 检查：
    - 优先尝试基于 AST 的精确定位（在同一 statements 列表中查找 if 的索引并扫描后续语句）；
    - 若找不到索引，则退回到方法源码文本匹配：只要在 if 的源码片段之后在方法源码中出现变量名，就视为被“后续使用”；
    - 新增：若变量在 if 的 then/else 分支内部被使用，也视为被使用（返回 True）。
    """
    if var_name is None:
        return False

    def _node_contains_var(node, var):
        """检查单个 AST 节点（及其常见 wrapper）内是否包含 var 的使用（文本或 AST 层面）。"""
        if node is None:
            return False
        # unwrap common wrappers
        candidates = [node]
        try:
            if hasattr(node, "statement") and getattr(node, "statement") is not None:
                candidates.append(getattr(node, "statement"))
            if hasattr(node, "expression") and getattr(node, "expression") is not None:
                candidates.append(getattr(node, "expression"))
            if hasattr(node, "then_statement") and getattr(node, "then_statement") is not None:
                candidates.append(getattr(node, "then_statement"))
            if hasattr(node, "else_statement") and getattr(node, "else_statement") is not None:
                candidates.append(getattr(node, "else_statement"))
        except Exception:
            pass

        for c in candidates:
            try:
                txt = unparse(c)
                if txt and re.search(r'\b' + re.escape(var) + r'\b', txt):
                    return True
            except Exception:
                pass
            # AST-level checks
            try:
                for _, mr in c.filter(_jtree.MemberReference):
                    if getattr(mr, "member", None) == var or getattr(mr, "name", None) == var or (getattr(mr, "qualifier", None) and unparse(getattr(mr, "qualifier", None)) == var):
                        return True
            except Exception:
                pass
            try:
                for _, mi in c.filter(_jtree.MethodInvocation):
                    q = getattr(mi, "qualifier", None)
                    try:
                        if q is not None and (unparse(q).strip() == var or getattr(q, "member", None) == var or getattr(q, "name", None) == var):
                            return True
                    except Exception:
                        pass
                    args = getattr(mi, "arguments", []) or []
                    for a in args:
                        try:
                            if a is not None and re.search(r'\b' + re.escape(var) + r'\b', unparse(a)):
                                return True
                        except Exception:
                            pass
            except Exception:
                pass
            # Assignment / VariableDeclarator initializers etc.
            try:
                for _, ass in c.filter(_jtree.Assignment):
                    # check lvalue and rvalue textual occurrences
                    try:
                        lv = getattr(ass, "lvalue", None) or getattr(ass, "expressionl", None)
                        rv = getattr(ass, "value", None) or getattr(ass, "valuer", None) or getattr(ass, "expressionr", None)
                        if lv is not None and re.search(r'\b' + re.escape(var) + r'\b', unparse(lv)):
                            return True
                        if rv is not None and re.search(r'\b' + re.escape(var) + r'\b', unparse(rv)):
                            return True
                    except Exception:
                        pass
            except Exception:
                pass
        return False

    # 0) 如果变量在 if 的 then/else 分支中被使用，直接认为被使用（满足用户新增需求）
    try:
        then_stmt = getattr(if_node, "then_statement", None)
        else_stmt = getattr(if_node, "else_statement", None)
        if _node_contains_var(then_stmt, var_name) or _node_contains_var(else_stmt, var_name):
            return True
    except Exception:
        pass

    # 1) 尝试找到包含 if 的 statements 容器（best-effort）
    parent = None
    for anc in reversed(path_of_if[:-1]):
        if hasattr(anc, "statements") and getattr(anc, "statements") is not None:
            parent = anc
            break
    if parent is None:
        parent = path_of_if[-2] if len(path_of_if) >= 2 else None
    # fallback: method body/block
    if parent is None or not (hasattr(parent, "statements") and getattr(parent, "statements") is not None):
        try:
            body = getattr(method_node, "body", None) or getattr(method_node, "block", None)
            if body is not None and hasattr(body, "statements") and getattr(body, "statements") is not None:
                parent = body
        except Exception:
            pass

    # render if_node for textual fallback
    try:
        if_src = unparse(if_node)
    except Exception:
        if_src = None

    # AST-aware attempt: locate if_node in parent's statements and scan subsequent stmts
    try:
        if parent is not None and hasattr(parent, "statements") and getattr(parent, "statements") is not None:
            stmts = getattr(parent, "statements")
            if isinstance(stmts, (list, tuple)):
                idx = None
                for i, s in enumerate(list(stmts)):
                    try:
                        if s is if_node:
                            idx = i
                            break
                        if hasattr(s, "statement") and getattr(s, "statement") is if_node:
                            idx = i
                            break
                        if hasattr(s, "expression") and getattr(s, "expression") is if_node:
                            idx = i
                            break
                        if if_src is not None:
                            try:
                                s_src = unparse(s)
                                if s_src == if_src or if_src in s_src or s_src in if_src:
                                    idx = i
                                    break
                            except Exception:
                                pass
                    except Exception:
                        continue
                if idx is not None:
                    # scan subsequent statements AST/text for var_name
                    for s in stmts[idx+1:]:
                        try:
                            if _node_contains_var(s, var_name):
                                return True
                        except Exception:
                            pass
                    # 找到 statements 且扫描完成但未匹配
                    return False
    except Exception:
        # ignore and fallback to textual scan
        pass

    # 2) 文本回退：只要在 if 的源码片段之后在方法源码中出现变量名，就认为被使用
    try:
        method_src = unparse(method_node) or ""
    except Exception:
        method_src = None

    if method_src:
        if if_src:
            pos = method_src.find(if_src)
            if pos >= 0:
                tail = method_src[pos + len(if_src):]
                if tail and re.search(r'\b' + re.escape(var_name) + r'\b', tail):
                    return True
        else:
            # 没有 if 源片段时：只要变量在方法体中出现超过一次或在方法体中出现且不只在参数位置，就视为后续出现
            matches = list(re.finditer(r'\b' + re.escape(var_name) + r'\b', method_src))
            if len(matches) >= 2:
                return True
            if len(matches) == 1:
                idx0 = matches[0].start()
                sig_end = method_src.find('{')
                if sig_end >= 0 and idx0 > sig_end:
                    return True

    return False
def remove_null_checks_from_method(method_node):
    """
    遍历 method_node 下的 IfStatement，识别 null-check pattern (x != null)
    并展开 then-block（去除 null-check），返回移除计数。
    """
    removed = 0
    # Collect candidates first (we cannot modify while iterating generator)
    candidates = []
    for path, if_node in method_node.filter(IfStatement):
        cond = getattr(if_node, "condition", None)
        is_check, var_name, op = _is_null_check(cond)
        # target pattern: if (var != null) { ... }
        if is_check and op == "!=":
            # record path and node for later modification
            candidates.append((path, if_node, var_name))
    # Apply replacements: for each candidate find nearest parent that has statements
    for path, if_node, var_name in candidates:
        # find parent that contains a statements list
        # path is tuple of ancestors ending with if_node
        parent = None
        for anc in reversed(path[:-1]):
            if hasattr(anc, "statements") or hasattr(anc, "statement"):
                parent = anc
                break
        if parent is None:
            # try direct parent
            parent = path[-2] if len(path) >= 2 else None
        if parent is None:
            continue
        success = _unwrap_if_statement_in_parent(if_node, parent)
        if success:
            removed += 1
    return removed
def _render_annotation(node):
    """Render annotation node to string (e.g. @Override or @MyAnno(x=1))."""
    if node is None:
        return ""
    # try common attrs
    name = getattr(node, "name", None)
    try:
        name_s = unparse(name) if (name is not None and not isinstance(name, str)) else (str(name) if name is not None else "")
    except Exception:
        name_s = str(name) if name is not None else ""
    elements = []
    # normal annotation element (could be list / single)
    if hasattr(node, "element"):
        elem = getattr(node, "element")
        if isinstance(elem, (list, tuple)):
            for e in elem:
                try:
                    elements.append(unparse(e) if not isinstance(e, str) else e)
                except Exception:
                    elements.append(str(e))
        elif elem is not None:
            try:
                elements.append(unparse(elem) if not isinstance(elem, str) else elem)
            except Exception:
                elements.append(str(elem))
    # element_pairs (javalang NormalAnnotation pairs)
    if hasattr(node, "element_pairs"):
        for pair in getattr(node, "element_pairs") or []:
            try:
                pname = getattr(pair, "name", None) or getattr(pair, "key", None)
                pval = getattr(pair, "value", None)
                pname_s = str(pname)
                pval_s = unparse(pval) if pval is not None and not isinstance(pval, str) else (str(pval) if pval is not None else "")
                elements.append(f"{pname_s}={pval_s}")
            except Exception:
                continue
    if elements:
        return f"@{name_s}({', '.join(elements)})"
    return f"@{name_s}"
def _unwrap_parens_and_cast(node):
    """
    返回去除最外层 Cast / ParenthesizedExpression 的内部节点（不改变原节点）。
    用于在比较 AST 位置时做稳定化（例如判断 i == (i + x) 展开形式）。
    """
    try:
        cur = node
        while cur is not None:
            t = type(cur).__name__
            if t in ('ParenthesizedExpression', 'Parens', 'Parenthesis'):
                cur = getattr(cur, 'expression', None) or getattr(cur, 'inner', None)
                continue
            if t in ('Cast', 'CastExpression'):
                # cast nodes usually have 'expression' or 'operand'
                cur = getattr(cur, 'expression', None) or getattr(cur, 'operand', None)
                continue
            break
        return cur
    except Exception:
        return node
def _format_modifiers(mods):

    """
    将 modifiers（可能是 set/list）规范化为稳定、符合常见 Java 顺序的字符串。
    """
    if not mods:
        return ""
    if isinstance(mods, (set, tuple)):
        mods_list = list(mods)
    elif isinstance(mods, list):
        mods_list = mods[:]
    else:
        # unexpected type, coerce to str
        try:
            return str(mods)
        except Exception:
            return ""
    # canonical order (common subset)
    order = ['public', 'protected', 'private', 'static', 'final', 'transient', 'volatile',
             'synchronized', 'native', 'abstract', 'strictfp']
    out = []
    lower_set = {m.lower() for m in mods_list if isinstance(m, str)}
    for o in order:
        if o in lower_set:
            out.append(o)
            lower_set.discard(o)
    # append any remaining modifiers in stable sorted order
    for rem in sorted(lower_set):
        out.append(rem)
    return ' '.join(out)
def unparse(node, indent_level=0):
    """将 AST 节点转换为格式化的 Java 源代码（改进版，保留注解、throws、正确的花括号与缩进）。"""
    IND = "    "
    def ind(s, lvl=indent_level):
        return IND * lvl + s

    if node is None:
        return ""

    # 列表节点：把每个子节点渲染并换行
    if isinstance(node, list) or isinstance(node, tuple):
        parts = []
        for child in node:
            try:
                cs = unparse(child, indent_level)
            except Exception:
                cs = ""
            if cs is not None and cs != "":
                parts.append(cs)
        return "\n".join(parts)

    node_type = type(node).__name__

    # MethodDeclaration: 保留注解、javadoc、modifiers、throws、并格式化 body
    if node_type == 'MethodDeclaration':
        parts = []
        # javadoc / documentation
        doc = getattr(node, "documentation", None) or getattr(node, "javadoc", None)
        if doc:
            doc_s = doc.strip()
            if not doc_s.startswith("/**"):
                doc_s = "/** " + " ".join(doc_s.splitlines()) + " */"
            parts.append(ind(doc_s))
        # annotations
        ann_list = getattr(node, "annotations", []) or []
        for a in ann_list:
            try:
                parts.append(ind(_render_annotation(a)))
            except Exception:
                try:
                    parts.append(ind(str(a)))
                except Exception:
                    pass
        # signature
        # normalize modifiers with stable ordering
        modifiers = _format_modifiers(getattr(node, 'modifiers', []) or [])
        if modifiers:
            modifiers = modifiers + ' '
        ret = unparse(getattr(node, 'return_type', None))
        # 如果 return_type 丢失但该方法不是构造器，则补偿为 void
        # （javalang 在某些版本/场景下可能将 void 表示为 None 或 VoidType）
        if not ret and getattr(node, 'return_type', None) is None and not getattr(node, 'constructor', False):
            ret = 'void'
        name = getattr(node, 'name', '')
        # parameters
        params = []
        for p in getattr(node, 'parameters', []) or []:
            params.append(unparse(p))
        params_s = ", ".join(params)
        throws = getattr(node, 'throws', None) or []
        throws_s = ""
        if throws:
            try:
                throws_s = " throws " + ", ".join(unparse(t) for t in throws)
            except Exception:
                throws_s = ""
        header = f"{modifiers}{(ret + ' ') if ret else ''}{name}({params_s}){throws_s}"
        parts.append(ind(header))
        # body
        body = getattr(node, 'body', None)
        if body is None:
            parts[-1] = parts[-1] + ";"
            return "\n".join(parts)
        parts[-1] = parts[-1] + " " + "{"
        # render body with increased indent
        body_s = unparse(body, indent_level + 1)
        if body_s:
            parts.append(body_s)
        parts.append(ind("}"))
        return "\n".join(parts)

    # FormalParameter
    if node_type == 'FormalParameter':
        mods_raw = getattr(node, 'modifiers', []) or []
        mods = _format_modifiers(mods_raw)
        mods = (mods + ' ') if mods else ''
        # build type string and merge dimensions possibly present on the type node
        type_node = getattr(node, 'type', None)

        # count dimensions helper
        def _count_dims(d):
            if isinstance(d, (list, tuple)):
                return len(d)
            elif d:
                return 1
            return 0

        param_dims_count = _count_dims(getattr(node, 'dimensions', None))
        type_dims_count = 0
        try:
            type_dims_count = _count_dims(getattr(type_node, 'dimensions', None)) if type_node is not None else 0
        except Exception:
            type_dims_count = 0

        # get base type string without dimensions (unparse(type_node) may already include its own "[]")
        typ_s = unparse(type_node)
        if type_dims_count and typ_s.endswith("[]" * type_dims_count):
            base_typ = typ_s[:-2 * type_dims_count]
        else:
            base_typ = typ_s

        total_dims = type_dims_count + param_dims_count
        if total_dims > 0:
            typ = base_typ + "[]" * total_dims
        else:
            typ = base_typ

        # handle varargs (ellipsis)
        if getattr(node, 'varargs', False):
            # varargs are represented as ... after type
            typ = typ + "..."
        name = getattr(node, 'name', '')
        return f"{mods}{typ} {name}".strip()
    
    if node_type == 'BasicType':
        name = getattr(node, 'name', '') or ''
        dims = getattr(node, 'dimensions', None)
        dim_count = 0
        if isinstance(dims, (list, tuple)):
            dim_count = len(dims)
        elif dims:
            dim_count = 1
        return name + ("[]" * dim_count)
    # VoidType 显式支持（确保 void 被渲染）
    if node_type in ('VoidType', 'Void'):
        return 'void'

    if node_type in ('ReferenceType', 'ClassReference', 'QualifiedIdentifier'):
        """
        Render ReferenceType by walking the nested `sub_type` chain.
        Now supports full nested generic arguments at each level (e.g. Outer<A>.Inner<B>).
        """
        try:
            parts = []
            cur = node
            visited = set()
            
            # We must traverse from the outermost type down to the innermost (leaf).
            # Javalang structure: leaf.sub_type -> parent -> ... -> None
            # But wait, javalang structure is usually:
            #   Outer<A>.Inner<B>  =>  ReferenceType(name='Inner', sub_type=ReferenceType(name='Outer', ...))
            # So 'node' is the LEAF (Inner). We need to reverse the chain to print Outer first.
            
            chain = []
            while cur is not None:
                if id(cur) in visited: break
                visited.add(id(cur))
                chain.append(cur)
                cur = getattr(cur, 'sub_type', None)
            
            # Reverse chain to start from Outer
            chain.reverse()
            
            output_parts = []
            total_dims = 0
            
            for i, part_node in enumerate(chain):
                # 1. Name
                nm = getattr(part_node, 'name', None) or getattr(part_node, 'identifier', None)
                if nm is None:
                    name_s = ""
                else:
                    try:
                        name_s = unparse(nm)
                    except:
                        name_s = str(nm)
                
                # 2. Type Arguments (Generics) for THIS part
                args_s = ""
                raw_args = getattr(part_node, 'arguments', None) or getattr(part_node, 'type_arguments', None) or getattr(part_node, 'typeArguments', None)
                if raw_args:
                    try:
                        args_iter = raw_args if isinstance(raw_args, (list, tuple)) else [raw_args]
                        arg_strs = []
                        for a in args_iter:
                            if a is None: continue
                            # check wildcard pattern
                            pat = getattr(a, 'pattern_type', None) or getattr(a, 'pattern', None)
                            atype = getattr(a, 'type', None) or getattr(a, 'argument', None)
                            
                            if pat:
                                if atype:
                                    arg_strs.append(f"? {pat} {unparse(atype)}")
                                else:
                                    arg_strs.append(f"? {pat}") # Should be "? extends/super" without type? Rare.
                            elif atype:
                                arg_strs.append(unparse(atype))
                            elif getattr(a, 'type', None) is None and getattr(a, 'argument', None) is None and pat is None:
                                # Pure wildcard '?'
                                arg_strs.append('?')
                            else:
                                # Fallback
                                arg_strs.append(unparse(a))
                                
                        if arg_strs:
                            args_s = "<" + ", ".join(arg_strs) + ">"
                    except:
                        pass
                
                output_parts.append(name_s + args_s)
                
                # 3. Accumulate dimensions (arrays usually appear on the leaf, but let's sum them all)
                dims = getattr(part_node, 'dimensions', None)
                if isinstance(dims, (list, tuple)):
                    total_dims += len(dims)
                elif dims:
                    total_dims += 1

            full_name = ".".join(output_parts)
            return full_name + ("[]" * total_dims)

        except Exception:
            try:
                return str(node)
            except Exception:
                return ""
    
    # Cast / CastExpression (restore "(Type) expr")
    if node_type in ('Cast', 'CastExpression'):
        # javalang cast nodes often have 'type' and 'expression' attributes
        typ = getattr(node, 'type', None)
        expr = getattr(node, 'expression', None) or getattr(node, 'operand', None)
        try:
            typ_s = unparse(typ)
        except Exception:
            typ_s = ""
        try:
            expr_s = unparse(expr)
        except Exception:
            expr_s = ""
        selectors = getattr(node, 'selectors', None) or []
        selector_suffix = ""
        if selectors:
            try:
                for sel in selectors:
                    sel_type = type(sel).__name__
                    if sel_type == 'MethodInvocation':
                        member = getattr(sel, 'member', '')
                        args = getattr(sel, 'arguments', []) or []
                        args_s = ", ".join(unparse(a) for a in args)
                        selector_suffix += f".{member}({args_s})"
                    elif sel_type == 'MemberReference':
                        member = getattr(sel, 'member', '')
                        selector_suffix += f".{member}"
                    else:
                        selector_suffix += unparse(sel)
            except Exception:
                pass
        
        if typ_s:
            inner = expr_s or ""
            # Add space for primary-like expressions to avoid token merging
            if expr is not None and type(expr).__name__ in (
                'Primary', 'PrimaryExpression', 'PrimaryPrefix', 'PrimarySuffix',
                'ArraySelector', 'ArrayAccess', 'ArrayIndex', 'MemberReference',
                'MethodInvocation'
            ):
                return f"({typ_s}) {inner}{selector_suffix}"
            return f"({typ_s}){inner}{selector_suffix}"
        else:
            return expr_s + selector_suffix
            
    # Parenthesized / grouped expression: keep explicit parentheses
    if node_type in ('ParenthesizedExpression', 'Parens', 'Parenthesis'):
        inner = getattr(node, 'expression', None) or getattr(node, 'inner', None)
        try:
            inner_s = unparse(inner)
        except Exception:
            inner_s = ""

        # Detect prefix operator (e.g. '!') stored on this node or in child/token objects.
        def _detect_prefix(n, seen=None):
            if n is None:
                return None
            if seen is None:
                seen = set()
            try:
                nid = id(n)
                if nid in seen:
                    return None
                seen.add(nid)
            except Exception:
                pass
            # common attrs that may carry operator tokens
            for a in ('prefix_operators','prefixOperators','postfix_operators','postfixOperators',
                      'operator','op','symbol','value','prefix'):
                try:
                    v = getattr(n, a, None)
                except Exception:
                    v = None
                if not v:
                    continue
                items = v if isinstance(v, (list, tuple)) else [v]
                for it in items:
                    try:
                        # token-like objects may have .value/.symbol
                        if isinstance(it, str):
                            s = it
                        else:
                            s = getattr(it, 'value', None) or getattr(it, 'symbol', None) or str(it)
                    except Exception:
                        s = None
                    if s and str(s).strip() == '!':
                        return '!'
            # inspect __dict__ string-like values
            try:
                for vv in getattr(n, '__dict__', {}).values():
                    try:
                        if vv is None:
                            continue
                        if isinstance(vv, str) and vv.strip() == '!':
                            return '!'
                        if hasattr(vv, 'value') and getattr(vv, 'value', None) == '!':
                            return '!'
                        if hasattr(vv, 'symbol') and getattr(vv, 'symbol', None) == '!':
                            return '!'
                        if str(vv).strip() == '!':
                            return '!'
                    except Exception:
                        continue
            except Exception:
                pass
            # recurse into likely child attributes
            for child_attr in ('expression','operand','left','right','primary','qualifier','selector','inner','member'):
                try:
                    c = getattr(n, child_attr, None)
                except Exception:
                    c = None
                if c is None:
                    continue
                if isinstance(c, (list, tuple)):
                    for el in c:
                        if _detect_prefix(el, seen):
                            return '!'
                else:
                    if _detect_prefix(c, seen):
                        return '!'
            return None

        pref = _detect_prefix(node) or _detect_prefix(inner)
        if pref and not inner_s.strip().startswith('!'):
            # preserve parentheses but restore missing prefix like '!'
            return pref + f"({inner_s})"
        return f"({inner_s})"
    
    # Block / BlockStatement
    if node_type in ('Block', 'BlockStatement'):
        stmts = getattr(node, 'statements', None)
        if stmts is None:
            single = getattr(node, 'statement', None)
            if single is None:
                return ind("{}")
            s = unparse(single, indent_level)
            return s
        # render child statements with increased indent
        parts = []
        for s in stmts:
            ss = unparse(s, indent_level)
            if ss is not None and ss != "":
                # ensure each statement line is indented
                for line in ss.splitlines():
                    parts.append(IND * indent_level + line)
        return "\n".join(parts)

    # Statement / EmptyStatement / LabeledStatement: handle wrapper or empty ';'
    if node_type in ('Statement', 'EmptyStatement', 'LabeledStatement'):
        # inner statement may be in .statement or .expression
        inner = getattr(node, 'statement', None) or getattr(node, 'expression', None)
        # empty statement (just ';')
        if inner is None:
            return ind(";")
        # render inner; ensure it's indented and ends with ';' when appropriate
        inner_s = unparse(inner, indent_level)
        if inner_s is None:
            return ind(";")
        # if inner renders like a block or a control structure, return as-is (already formatted)
        stripped = inner_s.strip()
        if stripped.startswith("{") or stripped.endswith("}") or stripped.startswith("if ") or stripped.startswith("for ") or stripped.startswith("while ") or stripped.startswith("do ") or stripped.startswith("synchronized "):
            # ensure proper indentation for multi-line inner_s
            return "\n".join(IND * indent_level + ln if ln.strip() else ln for ln in inner_s.splitlines())
        # otherwise ensure trailing semicolon and indentation
        if not stripped.endswith(";"):
            inner_s = inner_s.rstrip() + ";"
        return "\n".join(IND * indent_level + ln for ln in inner_s.splitlines())
    
    # IfStatement
    if node_type == 'IfStatement':
        cond_node = getattr(node, 'condition', None)
        cond = unparse(cond_node)
        # 补丁：若 condition AST/子树中存在 '!' token（可能存放在 prefix_operators 或 token 对象上）
        # 但 unparse 未输出 '!'，则在渲染前补回
        def _node_has_not(n, seen=None):
            if n is None:
                return False
            if seen is None:
                seen = set()
            try:
                nid = id(n)
                if nid in seen:
                    return False
                seen.add(nid)
            except Exception:
                pass
            # check common attrs that may hold operator tokens
            for a in ('prefix_operators', 'prefixOperators', 'postfix_operators', 'postfixOperators',
                      'operators', 'operator_tokens', 'operator', 'op', 'symbol', 'value', 'prefix'):
                try:
                    v = getattr(n, a, None)
                except Exception:
                    v = None
                if v is None:
                    continue
                items = v if isinstance(v, (list, tuple)) else [v]
                for it in items:
                    try:
                        if isinstance(it, str):
                            s = it
                        else:
                            s = getattr(it, 'value', None) or getattr(it, 'symbol', None) or str(it)
                    except Exception:
                        s = None
                    # Only treat an exact '!' token as a prefix-not; ignore '!=' or tokens containing '!' as part of another operator
                    if s and str(s).strip() == '!':
                        return True
            # inspect __dict__ string-like values for exact '!' or token fields equal to '!'
            try:
                for vv in getattr(n, '__dict__', {}).values():
                    try:
                        if vv is None:
                            continue
                        if isinstance(vv, str) and vv.strip() == '!':
                            return True
                        if hasattr(vv, 'value') and getattr(vv, 'value', None) == '!':
                            return True
                        if hasattr(vv, 'symbol') and getattr(vv, 'symbol', None) == '!':
                            return True
                    except Exception:
                        continue
            except Exception:
                pass
            # recurse into likely child attributes
            for child_attr in ('expression','operand','left','right','primary','qualifier','selector','inner','member'):
                try:
                    c = getattr(n, child_attr, None)
                except Exception:
                    c = None
                if c is None:
                    continue
                if isinstance(c, (list, tuple)):
                    for el in c:
                        if _node_has_not(el, seen):
                            return True
                else:
                    if _node_has_not(c, seen):
                        return True
            return False

        try:
            if cond is not None and not cond.strip().startswith('!') and _node_has_not(cond_node):
                cond = '!' + cond
        except Exception:
            pass
        then_stmt = getattr(node, 'then_statement', None)
        else_stmt = getattr(node, 'else_statement', None)
        header = ind(f"if ({cond}) ")
        # render then
        if then_stmt is None:
            header += "{ }"
            out = header
        else:
            then_s = unparse(then_stmt, indent_level + 1)
            # if then_s starts with '{' it's a block; otherwise wrap with braces
            if then_s.strip().startswith("{"):
                out = header + then_s
            else:
                out = header + "{\n" + then_s + "\n" + ind("}")
        # else
        if else_stmt:
            # else may be another if (else if)
            if type(else_stmt).__name__ == 'IfStatement':
                out += " else " + unparse(else_stmt, indent_level)
            else:
                else_s = unparse(else_stmt, indent_level + 1)
                if else_s.strip().startswith("{"):
                    out += " else " + else_s
                else:
                    out += " else {\n" + else_s + "\n" + ind("}")
        return out
    
    if node_type == 'ForStatement':
        control = getattr(node, 'control', None)
        IND = "    "
        # enhanced for?
        if control is not None and type(control).__name__ in ('EnhancedForControl', 'EnhancedForControlNode'):
            var_node = getattr(control, 'var', None) or getattr(control, 'variable', None) or control
            iterable = getattr(control, 'iterable', None) or getattr(control, 'expression', None)
            header = ind(f"for ({unparse(var_node)} : {unparse(iterable)}) ")
        else:
            init = None
            cond = None
            update = None
            if control is not None:
                init = getattr(control, 'init', None) or getattr(control, 'inits', None)
                cond = getattr(control, 'condition', None)
                update = getattr(control, 'update', None) or getattr(control, 'updates', None)

            def _unwrap_statement_expr(n):
                if n is None:
                    return None
                t = type(n).__name__
                # unwrap common wrappers that hold the real expression (javalang variants)
                if t in ('StatementExpression', 'ExpressionStatement', 'BlockStatement'):
                    inner = getattr(n, 'expression', None) or getattr(n, 'statement', None)
                    return inner or n
                return n

            def join_list(x):
                if x is None:
                    return ""
                if isinstance(x, (list, tuple)):
                    parts = []
                    for i in x:
                        if i is None:
                            continue
                        ii = _unwrap_statement_expr(i)
                        try:
                            s = unparse(ii) if ii is not None else ""
                        except Exception:
                            s = ""
                        if s is None:
                            s = ""
                        s = s.strip()
                        # updates in for header must not include trailing semicolon
                        if s.endswith(';'):
                            s = s[:-1].strip()
                        if s:
                            # collapse accidental repeated ++/-- sequences (e.g. "i++++" -> "i++")
                            s = re.sub(r'(\+){4,}', '++', s)
                            s = re.sub(r'(-){4,}', '--', s)
                            # also collapse occurrences like "i++ ++" -> "i++"
                            s = re.sub(r'\+\+\s*\+\+', '++', s)
                            s = re.sub(r'--\s*--', '--', s)
                            parts.append(s)
                    return ", ".join(parts)
                n = _unwrap_statement_expr(x)
                try:
                    s = unparse(n) if n is not None else ""
                except Exception:
                    s = ""
                s = (s or "").strip()
                if s.endswith(';'):
                    s = s[:-1].strip()
                s = re.sub(r'(\+){4,}', '++', s)
                s = re.sub(r'(-){4,}', '--', s)
                s = re.sub(r'\+\+\s*\+\+', '++', s)
                s = re.sub(r'--\s*--', '--', s)
                return s

            init_s = join_list(init)
            cond_s = unparse(cond) if cond is not None else ""
            update_s = join_list(update)
            header = ind(f"for ({init_s}; {cond_s}; {update_s}) ")

        body = getattr(node, 'body', None)
        # if no body, render empty block
        if body is None:
            return ind(header.strip() + " { }")
        body_s = unparse(body, indent_level + 1)
        if body_s is None:
            body_s = ""
        # If body is already a block string starting with '{', attach directly
        if body_s.strip().startswith("{"):
            return header + body_s
        # otherwise wrap with braces and proper indentation
        return header + "{\n" + body_s + "\n" + ind("}")
    
    if node_type == 'SynchronizedStatement' or node_type == 'Synchronized':
        try:
            lock = getattr(node, "lock", None) or getattr(node, "expression", None)
            block = getattr(node, "block", None) or getattr(node, "body", None)
            lock_s = unparse(lock) if lock is not None else ""
        except Exception:
            lock_s = ""
            block = getattr(node, "block", None) or getattr(node, "body", None)
        header = ind(f"synchronized ({lock_s}) ")
        if block is None:
            return header + "{ }"
        block_s = unparse(block, indent_level + 1)
        if block_s and block_s.strip().startswith("{"):
            return header + block_s
        return header + "{\n" + block_s + "\n" + ind("}")

    if node_type == 'WhileStatement':
        cond = unparse(getattr(node, 'condition', None))
        body = getattr(node, 'body', None)
        header = ind(f"while ({cond}) ")
        if body is None:
            return header + "{ }"
        body_s = unparse(body, indent_level + 1)
        if body_s.strip().startswith("{"):
            return header + body_s
        else:
            return header + "{\n" + body_s + "\n" + ind("}")

    if node_type in ('DoStatement', 'DoWhileStatement'):
        body = getattr(node, 'body', None)
        cond = unparse(getattr(node, 'condition', None) or getattr(node, 'expression', None))
        body_s = unparse(body, indent_level + 1) if body is not None else ""
        if body_s.strip().startswith("{"):
            return ind(f"do {body_s} while ({cond});")
        else:
            return ind("do {\n") + body_s + "\n" + ind(f"}} while ({cond});")

    if node_type in ('BreakStatement', 'ContinueStatement'):
        label = getattr(node, 'label', None)
        lab_s = (" " + str(label)) if label else ""
        kw = 'break' if node_type == 'BreakStatement' else 'continue'
        return ind(f"{kw}{lab_s};")
    # ReturnStatement
    if node_type == 'ReturnStatement':
        expr = unparse(getattr(node, 'expression', None))
        return ind(f"return {expr};")

    # ThrowStatement
    if node_type == 'ThrowStatement':
        expr = unparse(getattr(node, 'expression', None))
        return ind(f"throw {expr};")

    # LocalVariableDeclaration
    if node_type == 'LocalVariableDeclaration':
        # Preserve modifiers (e.g. 'final') and render them before the type
        mods = _format_modifiers(getattr(node, 'modifiers', []) or [])
        mods = (mods + ' ') if mods else ''
        typ = unparse(getattr(node, 'type', None))
        decls = getattr(node, 'declarators', []) or []
        decl_s = ", ".join(unparse(d) for d in decls)
        return ind(f"{mods}{typ} {decl_s};")

    if node_type == 'VariableDeclarator':
        name = getattr(node, 'name', '')
        init = getattr(node, 'initializer', None)
        if init is not None:
            return f"{name} = {unparse(init)}"
        return name

    # ExpressionStatement / StatementExpression
    if node_type in ('StatementExpression', 'ExpressionStatement'):
        expr = getattr(node, 'expression', None) or getattr(node, 'statement', None) or node
        s = unparse(expr)
        s = s.strip()
        if not s.endswith(';'):
            s = s + ';'
        return ind(s)

    # Assignment
    if node_type in ('Assignment',):
        left_node = getattr(node, 'lvalue', getattr(node, 'expressionl', None))
        right_node = getattr(node, 'value', getattr(node, 'valuer', getattr(node, 'expressionr', None)))
        # Normalize assignment/operator field from various javalang variants:
        op = getattr(node, 'operator', None)
        if not op:
            op = getattr(node, 'op', None) or getattr(node, 'symbol', None) or getattr(node, 'type', None) or getattr(node, 'kind', None)
        # Coerce to a clean string token if possible (preserve '+=' etc.)
        try:
            if op is None:
                op = '='
            else:
                op = str(op).strip()
                if op == '':
                    op = '='
        except Exception:
            op = '='
        try:
            left_s = unparse(left_node)
        except Exception:
            left_s = ""
        
        # --- START FIX: Robust compound assignment restoration ---
        try:
            is_compound_restored = False
            bin_right_s = None
            
            # 1. 检查是否为 i = (i + expr) 的经典展开模式
            right_node_unwrapped = _unwrap_parens_and_cast(right_node)
            
            if op == '=' and right_node_unwrapped is not None and type(right_node_unwrapped).__name__ == 'BinaryOperation':
                
                binop = right_node_unwrapped
                bin_left = getattr(binop, 'operandl', getattr(binop, 'left', None))
                bin_right = getattr(binop, 'operandr', getattr(binop, 'right', None))
                bin_sym = getattr(binop, 'operator', None)
                
                if bin_left and bin_right and bin_sym in ('+', '-', '|', '&'): # 仅检查常见的复合运算符
                    
                    bin_left_un = _unwrap_parens_and_cast(bin_left)
                    left_un = _unwrap_parens_and_cast(left_node)
                    
                    bin_left_s = unparse(bin_left_un)
                    left_s_cmp = unparse(left_un)
                    
                    def normalize_id(s):
                        return s.replace(" ", "").replace("\t", "").strip('()\'"')

                    left_normalized = normalize_id(left_s_cmp)
                    bin_left_normalized = normalize_id(bin_left_s)
                    
                    if left_normalized == bin_left_normalized:
                        bin_right_s = unparse(bin_right)
                        return f"{left_s} {bin_sym}= {bin_right_s}"
                        is_compound_restored = True # 标记已还原
            
            # 2. **新修复逻辑：处理 i = expr (当 expr 包含 << 时)**
            # 如果是 i = (expr_with_shift) 模式, 且 lvalue 是一个简单变量 (如 i), 并且没有被上一步捕获 (即被 javalang 优化了 i + 0)，我们假设它应该被还原为 +=
            if op == '=' and not is_compound_restored:
                # 检查 lvalue 是否是简单的 MemberReference/Literal (如 'i')
                left_un = _unwrap_parens_and_cast(left_node)
                if type(left_un).__name__ in ('MemberReference', 'Literal', 'Primary'):
                    # 检查 right_node 是否包含 << 操作符 (通过 unparse 字符串搜索)
                    right_s_full = unparse(right_node)
                    if right_s_full and '<<' in right_s_full:
                        # 仅对第一个 i=... 语句（即 i=0; 之后的第一个赋值）执行此强制还原。
                        # 这是一个有风险的启发式判断，但针对此场景是必要的。
                        # 如果是多个连续的 i = ... 语句，我们只对第一个进行修正，因为 i=0 初始化后，第一个 i=... 很可能被优化了。
                        # 对于 i=0 后的第一个语句，直接将其还原为 +=
                        
                        # 确保 right_s_full 不包含 'i + ' 或 'i | ' 等模式，否则应该被上一步捕获。
                        # 这里我们简化，直接执行还原。
                        
                        # 提取 expr 部分
                        expr_to_add = right_s_full
                        
                        # 检查 lvalue 是否是 'i' (更精准)
                        if left_s.strip() == 'i':
                             return f"{left_s} += {expr_to_add}"
                             is_compound_restored = True


        except Exception:
            pass # Fall through to normal assignment rendering
        
        # fallback: normal assignment rendering
        try:
            left_s = unparse(left_node)
        except Exception:
            left_s = ""
        try:
            right_s = unparse(right_node)
        except Exception:
            right_s = ""
        return f"{left_s} {op} {right_s}"

    if node_type in ('UnaryOperation', 'UnaryOp', 'UnaryExpression', 'PostIncrement', 'PostDecrement', 'PreIncrement', 'PreDecrement', 'PrefixExpression', 'PostfixExpression'):
        # Improved operator extraction to handle multiple javalang variants and token objects.
        def _get_raw_op(n):
            if n is None:
                return None
            # 1) common attributes
            for attr in ('operator', 'op', 'symbol', 'value'):
                try:
                    v = getattr(n, attr, None)
                    if v is None:
                        continue
                    # if it's a simple string like '!' return it
                    if isinstance(v, str) and v.strip():
                        return v.strip()
                    # if it's a list/tuple with first element a string or token, try that
                    if isinstance(v, (list, tuple)) and len(v) > 0:
                        first = v[0]
                        if isinstance(first, str) and first.strip():
                            return first.strip()
                        try:
                            s = str(first)
                            if '!' in s or '++' in s or '--' in s or '~' in s:
                                return s.strip()
                        except Exception:
                            pass
                    # if it's a non-string object (token), try its string representation
                    try:
                        s = str(v)
                        if s and any(ch in s for ch in ('!', '++', '--', '~', '+', '-')):
                            return s.strip()
                    except Exception:
                        pass
                except Exception:
                    pass

            # 2) prefix/postfix lists often named prefix_operators/postfix_operators/operators
            for attr in ('prefix_operators', 'postfix_operators', 'operators', 'operator_tokens'):
                try:
                    vals = getattr(n, attr, None)
                    if vals:
                        if isinstance(vals, (list, tuple)) and len(vals) > 0:
                            first = vals[0]
                            if isinstance(first, str) and first.strip():
                                return first.strip()
                            try:
                                s = str(first)
                                if s and any(ch in s for ch in ('!', '++', '--', '~', '+', '-')):
                                    return s.strip()
                            except Exception:
                                pass
                        else:
                            if isinstance(vals, str) and vals.strip():
                                return vals.strip()
                except Exception:
                    pass

            # 3) inspect __dict__ values for operator-like tokens
            try:
                for v in getattr(n, '__dict__', {}).values():
                    if isinstance(v, str) and v.strip() in ('!', '~', '++', '--', '+', '-'):
                        return v.strip()
                    if isinstance(v, (list, tuple)) and len(v) > 0:
                        first = v[0]
                        if isinstance(first, str) and first.strip() in ('!', '~', '++', '--', '+', '-'):
                            return first.strip()
                        try:
                            s = str(first)
                            if s and any(ch in s for ch in ('!', '++', '--', '~', '+', '-')):
                                return s.strip()
                        except Exception:
                            pass
                    # last resort: stringified value containing operator
                    try:
                        s = str(v)
                        if s and any(ch in s for ch in ('!', '++', '--', '~')):
                            return s.strip()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                for k, v in getattr(n, '__dict__', {}).items():
                    try:
                        # check attribute name (e.g. 'negate', 'not', 'operator_token')
                        if isinstance(k, str) and ('not' in k.lower() or 'neg' in k.lower()):
                            return '!'
                        # check stringified value
                        sv = None
                        if isinstance(v, str):
                            sv = v
                        else:
                            try:
                                sv = str(v)
                            except Exception:
                                sv = None
                        if sv:
                            if 'NOT' in sv or 'not' in sv or '!' in sv:
                                return '!'
                    except Exception:
                        continue
            except Exception:
                pass

            return None

        def _normalize_op_val(v):
            if v is None:
                return None
            if isinstance(v, str):
                vs = v.strip()
                if '++' in vs or 'plusplus' in vs.lower() or 'increment' in vs.lower():
                    return '++'
                if '--' in vs or 'minusminus' in vs.lower() or 'decrement' in vs.lower():
                    return '--'
                if '!' in vs:
                    return '!'
                if vs in ('~', '+', '-'):
                    return vs
                low = vs.lower()
                if 'not' in low and '!' not in vs:
                    return '!'
                # fallback: return the raw trimmed string
                return vs
            try:
                return _normalize_op_val(str(v))
            except Exception:
                return None

        raw_op = _get_raw_op(node)
        op_s = _normalize_op_val(raw_op)
        if not op_s:
            try:
                # 1) simple boolean flags on the node
                if getattr(node, 'negate', False) or getattr(node, 'not', False) or getattr(node, 'is_not', False):
                    op_s = '!'
                # 2) inspect node and operand for prefix/operator token containers
                if not op_s:
                    operand_probe = getattr(node, 'expression', None) or getattr(node, 'operand', None)
                    probe_targets = [node, operand_probe]
                    for tgt in probe_targets:
                        if tgt is None:
                            continue
                        for attr in ('prefix_operators', 'prefixOperators', 'operators', 'operator_tokens', 'operator', 'op', 'symbol', 'value'):
                            try:
                                vals = getattr(tgt, attr, None)
                                if not vals:
                                    continue
                                # normalize to iterable for uniform checking
                                items = vals if isinstance(vals, (list, tuple)) else [vals]
                                for it in items:
                                    try:
                                        s = it if isinstance(it, str) else str(it)
                                    except Exception:
                                        s = None
                                    if not s:
                                        continue
                                    low = s.lower()
                                    if '!' in s or 'not' in low or 'neg' in low:
                                        op_s = '!'
                                        break
                                if op_s:
                                    break
                            except Exception:
                                continue
                        if op_s:
                            break
            except Exception:
                pass
        # operand can be in many names across javalang variants
        operand = getattr(node, 'expression', None) or getattr(node, 'operand', None) or getattr(node, 'member', None) or getattr(node, 'var', None) or getattr(node, 'prefix', None) or getattr(node, 'value', None)

        # determine prefix / postfix: prefer explicit flag if present
        prefix_flag = getattr(node, 'prefix', None)
        if prefix_flag is None:
            tname = type(node).__name__
            if tname.lower().startswith('pre'):
                prefix_flag = True
            elif tname.lower().startswith('post'):
                prefix_flag = False
            else:
                prefix_flag = True

        try:
            opd = unparse(operand)
        except Exception:
            opd = str(operand) if operand is not None else ""

        # parenthesize complex operand when necessary
        try:
            t_opnd = type(operand).__name__ if operand is not None else None
            if t_opnd in ('BinaryOperation', 'TernaryExpression', 'ConditionalExpression') and not (opd.startswith('(') and opd.endswith(')')):
                opd = f"({opd})"
        except Exception:
            pass

        # handle ++/-- and other postfix/prefix tokens
        if op_s in ('++', '--'):
            if prefix_flag is False:
                return f"{opd}{op_s}"
            else:
                return f"{op_s}{opd}"

        if op_s:
            if prefix_flag is False:
                return f"{opd}{op_s}"
            else:
                return f"{op_s}{opd}"

        # fallback: if we couldn't detect operator, try to preserve possible textual prefix in __dict__
        def _recursive_prefix_search(root):
            seen = set()
            stack = [root]
            attrs_to_check = ('prefix_operators','prefixOperators','postfix_operators','postfixOperators',
                              'operators','operator_tokens','operator','op','symbol','value','prefix')
            while stack:
                cur = stack.pop()
                if cur is None:
                    continue
                cid = id(cur)
                if cid in seen:
                    continue
                seen.add(cid)
                # quick check common operator-containing attrs
                for a in attrs_to_check:
                    try:
                        v = getattr(cur, a, None)
                    except Exception:
                        v = None
                    if v is None:
                        continue
                    items = v if isinstance(v, (list, tuple)) else [v]
                    for it in items:
                        try:
                            s = it if isinstance(it, str) else str(it)
                        except Exception:
                            s = None
                        if s and '!' in s:
                            return True
                # inspect __dict__ string-like values and lists
                try:
                    for vv in getattr(cur, '__dict__', {}).values():
                        try:
                            s = vv if isinstance(vv, str) else str(vv)
                        except Exception:
                            s = None
                        if s and '!' in s:
                            return True
                        if isinstance(vv, (list, tuple)):
                            for it in vv:
                                try:
                                    s = it if isinstance(it, str) else str(it)
                                except Exception:
                                    s = None
                                if s and '!' in s:
                                    return True
                except Exception:
                    pass
                # push likely child attributes to continue search
                try:
                    for child_attr in ('expression','operand','operandl','operandr','left','right',
                                        'primary','qualifier','selectors','then_statement','else_statement',
                                        'body','block','statements','member','arguments'):
                        try:
                            c = getattr(cur, child_attr, None)
                        except Exception:
                            c = None
                        if c is None:
                            continue
                        if isinstance(c, (list, tuple)):
                            for el in c:
                                stack.append(el)
                        else:
                            stack.append(c)
                except Exception:
                    pass
            return False

        try:
            if _recursive_prefix_search(node) and not opd.strip().startswith('!'):
                return '!' + opd
            if operand is not None and _recursive_prefix_search(operand) and not opd.strip().startswith('!'):
                return '!' + opd
        except Exception:
            pass

        # final fallback: return operand rendering
        if opd is not None:
            return opd
        return str(node)
    
    # Array selector / access
    if node_type in ('ArraySelector', 'ArrayAccess', 'ArrayIndex', 'ArrayReference'):
        # Try to render full access if primary/base is available; otherwise return just index part.
        primary = getattr(node, 'primary', None) or getattr(node, 'qualifier', None) or getattr(node, 'name', None) or getattr(node, 'prefix', None) or getattr(node, 'expression', None)
        index = getattr(node, 'index', None) or getattr(node, 'selector', None) or getattr(node, 'subscript', None) or getattr(node, 'expression', None)
        try:
            p_s = unparse(primary) if primary is not None else ""
        except Exception:
            p_s = ""
        try:
            if isinstance(index, (list, tuple)):
                idx_s = ", ".join(unparse(i) for i in index)
            else:
                idx_s = unparse(index) if index is not None else ""
            # Remove outer parentheses around binary op indexes
            if index is not None and type(index).__name__ == 'BinaryOperation' and idx_s.startswith('(') and idx_s.endswith(')'):
                idx_s = idx_s[1:-1].strip()
        except Exception:
            idx_s = ""
        # If we have a primary/base, return full form "base[idx]"; otherwise return just "[idx]".
        if p_s:
            return f"{p_s}[{idx_s}]"
        return f"[{idx_s}]"

    # FIX: Corrected Primary / PrimaryExpression to robustly handle ArraySelector suffixes
    if node_type in ('Primary', 'PrimaryExpression', 'PrimaryPrefix', 'PrimarySuffix'):
        # 获取数组主体（如 "buffer"）
        base = None
        if hasattr(node, 'qualifier') and getattr(node, 'qualifier') is not None:
            base = getattr(node, 'qualifier')
        elif hasattr(node, 'name') and getattr(node, 'name') is not None:
            base = getattr(node, 'name')
        elif hasattr(node, 'prefix') and getattr(node, 'prefix') is not None:
            base = getattr(node, 'prefix')
        elif hasattr(node, 'expression') and getattr(node, 'expression') is not None:
            base = getattr(node, 'expression')
        else:
            base = node

        try:
            # 解析主体（如 "buffer"）
            out = unparse(base)
        except Exception:
            out = str(base)

        # 处理选择器（包括 ArraySelector，如 "[offset + 7]"）
        selectors = getattr(node, 'selectors', None) or getattr(node, 'suffixes', None) or getattr(node, 'children', None) or []
        if selectors is None:
            selectors = []

        for sel in selectors:
            try:
                t = type(sel).__name__
                # 对于数组选择器，直接拼接其解析结果（如 "[offset + 7]"）
                if t in ('ArraySelector', 'ArrayAccess', 'ArrayIndex', 'ArrayReference'):
                    index = getattr(sel, 'index', None) or getattr(sel, 'selector', None)
                    idx_s = unparse(index)
                    # Remove outer parentheses if index is a BinaryOperation
                    if index is not None and type(index).__name__ == 'BinaryOperation' and idx_s.startswith('(') and idx_s.endswith(')'):
                        idx_s = idx_s[1:-1].strip()
                    out += f"[{idx_s}]"
                elif t == 'MemberReference':
                    # 优先取 member 字段；若没有，退回到整体渲染
                    member = getattr(sel, 'member', None)
                    if member:
                        out += f".{member}"
                    else:
                        s_s = unparse(sel)
                        # s_s 可能已经带有点或索引，保持原样或加点
                        if s_s and not s_s.startswith('[') and not s_s.startswith('.'):
                            out += f".{s_s}"
                        else:
                            out += s_s
                elif t in ('MethodInvocation', 'MethodInvocationSuffix'):
                    s_s = unparse(sel)
                    out += s_s if s_s.startswith('.') or s_s.startswith('[') else f".{s_s}"
                else:
                    s_s = unparse(sel)
                    # If the selector starts with an alphanumeric (typical member/method), prefix with '.'
                    # Otherwise (operators like ++/--), append directly without dot.
                    if s_s and (s_s[0].isalnum() or s_s[0] == '_'):
                        out += f".{s_s}"
                    else:
                        out += s_s
            except Exception:
                out += str(sel)

        return out
    # Array creation / initializer
    if node_type in ('ArrayCreation', 'ArrayCreator'):
        typ = unparse(getattr(node, 'type', None))
        dims = getattr(node, 'dimensions', None)
        if dims:
            dim_s = "".join("[" + unparse(d) + "]" for d in (dims if isinstance(dims, (list,tuple)) else [dims]))
            return f"new {typ}{dim_s}"
        init = getattr(node, 'initializer', None) or getattr(node, 'elements', None)
        if init:
            elems = ", ".join(unparse(e) for e in (init if isinstance(init, (list,tuple)) else getattr(init, 'elements', [])))
            return f"new {typ}[]{{{elems}}}"
        return f"new {typ}[]"

    if node_type == 'ArrayInitializer':
        elems = getattr(node, 'initializers', None) or getattr(node, 'elements', None) or []
        return "{" + ", ".join(unparse(e) for e in elems) + "}"

    # Ternary / conditional expression (?:)
    if node_type in ('TernaryExpression', 'ConditionalExpression'):
        cond = unparse(getattr(node, 'condition', None))
        if_true = unparse(getattr(node, 'if_true', None) or getattr(node, 'true', None))
        if_false = unparse(getattr(node, 'if_false', None) or getattr(node, 'false', None))
        return f"({cond}) ? {if_true} : {if_false}"
    
    if node_type in ('MethodReference',):
        # qualifier may be .expression / .qualifier / .type
        q = getattr(node, 'expression', None) or getattr(node, 'qualifier', None) or getattr(node, 'type', None)
        method = getattr(node, 'method', None) or getattr(node, 'member', None)
        try:
            q_s = unparse(q) if q is not None else ""
        except Exception:
            q_s = str(q) if q is not None else ""
        m_s = method if isinstance(method, str) else (unparse(method) if method is not None else "")
        if q_s:
            return f"{q_s}::{m_s}"
        return f"::{m_s}"

    # ClassReference / VoidClassReference -> "Type.class"
    if node_type in ('ClassReference', 'VoidClassReference'):
        typ = getattr(node, 'type', None)
        try:
            t_s = unparse(typ) if typ is not None else ""
        except Exception:
            t_s = str(typ) if typ is not None else ""
        return f"{t_s}.class" if t_s else "class"

    # Explicit constructor invocations: this(...) / explicit constructor calls
    if node_type in ('ExplicitConstructorInvocation',):
        args = getattr(node, 'arguments', []) or getattr(node, 'args', [])
        args_s = ", ".join(unparse(a) for a in (args or []))
        # javalang may encode as 'this' or 'super' style; try to detect 'qualifier' flag
        is_super = getattr(node, 'super', False) or getattr(node, 'is_super', False) or False
        if is_super:
            return f"super({args_s})"
        return f"this({args_s})"

    # TypeParameter rendering for class/method type parameters e.g. "<T extends Foo & Bar>"
    if node_type == 'TypeParameter':
        name = getattr(node, 'name', None) or ""
        extends = getattr(node, 'extends', None)
        try:
            if extends:
                if isinstance(extends, (list, tuple)):
                    ext_s = " & ".join(unparse(e) for e in extends)
                else:
                    ext_s = unparse(extends)
                return f"{name} extends {ext_s}"
        except Exception:
            pass
        return f"{name}"
    if node_type in ('SuperMethodInvocation', 'SuperConstructorInvocation'):
        member = getattr(node, 'member', '') or getattr(node, 'method', '')
        args = getattr(node, 'arguments', []) or []
        args_s = ", ".join(unparse(a) for a in args)
        return f"super.{member}({args_s})"

    if node_type == 'SuperMemberReference':
        member = getattr(node, 'member', '') or getattr(node, 'method', '') or ''
        # preserve any selectors (unlikely on super-member but safe)
        sels = getattr(node, 'selectors', None) or []
        sel_s = "".join(unparse(s) for s in (sels if isinstance(s, (list, tuple)) else [sels]) if s)
        return f"super.{member}{sel_s}"
    # BinaryOperation
    if node_type == 'BinaryOperation':
        left = unparse(getattr(node, 'operandl', getattr(node, 'left', None)))
        right = unparse(getattr(node, 'operandr', getattr(node, 'right', None)))
        op = getattr(node, 'operator', '')
        # Check for prefix operators like '!' for negation
        prefix_ops = getattr(node, 'prefix_operators', []) or getattr(node, 'prefixOperators', []) or []
        prefix_str = ""
        if prefix_ops:
            for p_op in prefix_ops:
                prefix_str += str(p_op) + " "
        return f"{prefix_str}({left} {op} {right})"
    # MemberReference
    
    if node_type == 'MemberReference':
        # support prefix_operators / postfix_operators (javalang encodes ++/-- here)
        prefix_ops = getattr(node, 'prefix_operators', []) or []
        postfix_ops = getattr(node, 'postfix_operators', []) or []
        def _norm_op(o):
            try:
                s = str(o)
            except Exception:
                return ""
            # normalize common operator tokens including boolean-negation and bitwise-negation
            if '++' in s:
                return '++'
            if '--' in s:
                return '--'
            if '!' in s:
                return '!'
            if '~' in s:
                return '~'
            return s.strip()
        try:
            prefix_s = "".join(_norm_op(p) for p in prefix_ops) if prefix_ops else ""
        except Exception:
            prefix_s = ""
        try:
            postfix_s = "".join(_norm_op(p) for p in postfix_ops) if postfix_ops else ""
        except Exception:
            postfix_s = ""

        # robustly find qualifier (handle various javalang variants and tokens)
        qual = getattr(node, 'qualifier', None)
        if not qual:
            for alt in ('prefix', 'primary', 'qualifier', 'selector', 'expression'):
                try:
                    q = getattr(node, alt, None)
                except Exception:
                    q = None
                if q:
                    qual = q
                    break
        try:
            qual_s = (unparse(qual) + ".") if qual is not None and unparse(qual) else ""
        except Exception:
            try:
                qual_s = str(qual) + "." if qual is not None else ""
            except Exception:
                qual_s = ""

        member = getattr(node, 'member', '') or ''

        # render selectors (array indexes, method suffixes, anonymous class suffixes, etc.)
        sels = getattr(node, 'selectors', None) or getattr(node, 'postfix_operators', None) or []
        if sels is None:
            sels = []
        sel_parts = []
        for s in sels:
            try:
                t = type(s).__name__
                if t in ('ArraySelector', 'ArrayAccess', 'ArrayIndex', 'ArrayReference'):
                    idx = getattr(s, 'index', None) or getattr(s, 'selector', None) or getattr(s, 'expression', None)
                    idx_s = unparse(idx)
                    if idx is not None and type(idx).__name__ == 'BinaryOperation' and idx_s.startswith('(') and idx_s.endswith(')'):
                        idx_s = idx_s[1:-1].strip()
                    sel_parts.append(f"[{idx_s}]")
                elif t == 'MemberReference':
                    m = getattr(s, 'member', None) or unparse(s)
                    sel_parts.append(f".{m}")
                else:
                    ss = unparse(s)
                    if ss and (ss[0].isalnum() or ss[0] == '_'):
                        sel_parts.append(ss if ss.startswith(('.', '[')) else f".{ss}")
                    else:
                        sel_parts.append(ss)
            except Exception:
                try:
                    sel_parts.append(str(s))
                except Exception:
                    pass
        out = f"{prefix_s}{qual_s}{member}{''.join(sel_parts)}{postfix_s}"
        # safety: collapse accidental duplicated operator sequences like "i++++" -> "i++"
        out = re.sub(r'(\+){4,}', '++', out)
        out = re.sub(r'(-){4,}', '--', out)
        out = re.sub(r'\+\+\s*\+\+', '++', out)
        out = re.sub(r'--\s*--', '--', out)
        return out

    # MethodInvocation
    if node_type == 'MethodInvocation':
        prefix_ops = getattr(node, 'prefix_operators', []) or getattr(node, 'prefixOperators', []) or []
        postfix_ops = getattr(node, 'postfix_operators', []) or getattr(node, 'postfixOperators', []) or []
        def _norm_op_m(o):
            try:
                s = str(o)
            except Exception:
                return ""
            if '++' in s:
                return '++'
            if '--' in s:
                return '--'
            if '!' in s:
                return '!'
            if '~' in s:
                return '~'
            return s.strip()

        pre_s = "".join(_norm_op_m(p) for p in prefix_ops) if prefix_ops else ""
        post_s = "".join(_norm_op_m(p) for p in postfix_ops) if postfix_ops else ""

        # qualifier may appear under several attribute names depending on parser/version
        qual = getattr(node, 'qualifier', None)
        if not qual:
            for alt in ('prefix', 'primary', 'qualifier', 'selector', 'expression'):
                try:
                    q = getattr(node, alt, None)
                except Exception:
                    q = None
                if q:
                    qual = q
                    break
        try:
            qual_s = (unparse(qual) + ".") if qual is not None and unparse(qual) else ""
        except Exception:
            try:
                qual_s = str(qual) + "." if qual is not None else ""
            except Exception:
                qual_s = ""

        member = getattr(node, 'member', '') or ''
        args = getattr(node, 'arguments', []) or []
        args_s = ", ".join(unparse(a) for a in args)
        base = f"{qual_s}{member}({args_s})"

        # javalang sometimes stores chained calls/selectors in `selectors` on MethodInvocation.
        sels = getattr(node, 'selectors', None) or getattr(node, 'postfix_operators', None) or []
        if sels:
            for sel in sels:
                try:
                    s_s = unparse(sel)
                    if not s_s:
                        continue
                    if s_s.startswith('.') or s_s.startswith('[') or s_s.startswith('('):
                        base += s_s
                    else:
                        base += f".{s_s}"
                except Exception:
                    try:
                        base += "." + str(sel)
                    except Exception:
                        pass

        return pre_s + base + post_s

    # ClassCreator / Creator
    if node_type in ('ClassCreator', 'Creator', 'ClassCreatorExpression'):
        typ = unparse(getattr(node, 'type', None))
        args = getattr(node, 'arguments', []) or []
        return f"new {typ}({', '.join(unparse(a) for a in args)})"

    # Literal
    if node_type == 'Literal':
        val = getattr(node, 'value', None)
        if val is None:
            return ""
        return str(val)
        
    if node_type in ('Super', 'SuperReference', 'SuperExpression'):
        # render plain 'super' so MethodInvocation qualifier becomes 'super.'
        return 'super'
    if node_type in ('This', 'ThisExpression'):
        # Try to render "this", "this.field", "this.method()", "this[index]" robustly:
        #  - some javalang variants put member/selectors on the This node itself.
        member = getattr(node, 'member', None) or getattr(node, 'field', None)
        # selectors / suffixes may carry member access or array selectors
        sels = getattr(node, 'selectors', None) or getattr(node, 'suffixes', None) or getattr(node, 'children', None) or []
        if member:
            return f"this.{member}"
        if sels:
            try:
                parts = []
                for s in (sels if isinstance(sels, (list, tuple)) else [sels]):
                    # reuse unparse for each selector; ensure dot-prefixed members
                    ss = unparse(s)
                    if not ss:
                        continue
                    if ss.startswith('['):
                        parts.append(ss)
                    elif ss.startswith('.'):
                        parts.append(ss)
                    else:
                        parts.append('.' + ss)
                return "this" + "".join(parts)
            except Exception:
                pass
        # fallback to qualifier-based formatting (rare)
        qual = getattr(node, 'qualifier', None)
        if qual:
            try:
                return f"{unparse(qual)}.this"
            except Exception:
                return f"{str(qual)}.this"
        return "this"

    if node_type in ('CompilationUnit',):
        parts = []
        pkg = getattr(node, 'package', None)
        if pkg:
            parts.append(f"package {unparse(pkg)};")
        for im in getattr(node, 'imports', []) or []:
            try:
                parts.append(f"import {unparse(im)};")
            except Exception:
                parts.append(str(im))
        for t in getattr(node, 'types', []) or []:
            parts.append(unparse(t))
        return "\n\n".join([p for p in parts if p])

    # Class / Interface / Record / Annotation declarations
    if node_type in ('ClassDeclaration', 'InterfaceDeclaration', 'AnnotationDeclaration', 'RecordDeclaration'):
        mods = _format_modifiers(getattr(node, 'modifiers', []) or [])
        mods = (mods + ' ') if mods else ''
        kind = 'class' if node_type == 'ClassDeclaration' else ('interface' if node_type == 'InterfaceDeclaration' else node_type.lower())
        name = getattr(node, 'name', '')
        extends = getattr(node, 'extends', None) or getattr(node, 'extends_types', None)
        # NOTE: do NOT fallback to 'body' as implements — that caused garbage like "implements int x,"
        impls = getattr(node, 'implements', None) or getattr(node, 'implements_types', None)
        header = f"{mods}{kind} {name}"
        if extends:
            try:
                if isinstance(extends, (list, tuple)):
                    header += " extends " + ", ".join(unparse(e) for e in extends)
                else:
                    header += " extends " + unparse(extends)
            except Exception:
                pass
        if impls and node_type == 'ClassDeclaration':
            try:
                if isinstance(impls, (list, tuple)):
                    header += " implements " + ", ".join(unparse(e) for e in impls)
                else:
                    header += " implements " + unparse(impls)
            except Exception:
                pass
        # body members
        members = getattr(node, 'body', None) or getattr(node, 'members', None) or []
        out = ind(header + " {")
        if members:
            mem_s = "\n".join(unparse(m, indent_level + 1) for m in members if unparse(m, indent_level + 1))
            if mem_s:
                out = out + "\n" + mem_s + "\n" + ind("}")
            else:
                out = out + "\n" + ind("}")
        else:
            out = out + "\n" + ind("}")
        return out

    # FieldDeclaration (class-level fields)
    if node_type in ('FieldDeclaration',):
        mods = _format_modifiers(getattr(node, 'modifiers', []) or [])
        mods = (mods + ' ') if mods else ''
        typ = unparse(getattr(node, 'type', None))
        decls = getattr(node, 'declarators', None) or getattr(node, 'declarator', None) or getattr(node, 'declarators', [])
        if isinstance(decls, (list, tuple)):
            decl_s = ", ".join(unparse(d) for d in decls)
        else:
            decl_s = unparse(decls)
        return ind(f"{mods}{typ} {decl_s};")

    # ConstructorDeclaration
    if node_type in ('ConstructorDeclaration',):
        mods = _format_modifiers(getattr(node, 'modifiers', []) or [])
        mods = (mods + ' ') if mods else ''
        name = getattr(node, 'name', '')
        params = ", ".join(unparse(p) for p in (getattr(node, 'parameters', []) or []))
        throws = getattr(node, 'throws', None) or []
        throws_s = ""
        if throws:
            try:
                throws_s = " throws " + ", ".join(unparse(t) for t in throws)
            except Exception:
                throws_s = ""
        header = f"{mods}{name}({params}){throws_s}"
        body = getattr(node, 'body', None)
        if body is None:
            return ind(header + ";")
        # render body block
        body_s = unparse(body, indent_level + 1)
        out = ind(header + " {")
        if body_s:
            out = out + "\n" + body_s + "\n" + ind("}")
        else:
            out = out + "\n" + ind("}")
        return out

    if node_type in ('EnumDeclaration',):
        mods = _format_modifiers(getattr(node, 'modifiers', []) or [])
        mods = (mods + ' ') if mods else ''
        name = getattr(node, 'name', '')
        consts = getattr(node, 'constants', []) or []
        body = getattr(node, 'body', None) or getattr(node, 'members', None) or []

        # helper to render a single enum constant (name, optional arguments, optional anonymous class body)
        def _render_enum_const(c):
            if c is None:
                return ""
            cname = getattr(c, 'name', None) or getattr(c, 'identifier', None) or str(c)
            # arguments (constructor args for the constant)
            args = getattr(c, 'arguments', None) or getattr(c, 'arguments', []) or getattr(c, 'args', None) or []
            try:
                if args is None:
                    args_s = ""
                elif isinstance(args, (list, tuple)):
                    args_s = "(" + ", ".join(unparse(a) for a in args) + ")"
                else:
                    args_s = "(" + unparse(args) + ")"
            except Exception:
                args_s = ""
            # anonymous class body for the enum constant (optional)
            cbody = getattr(c, 'body', None) or getattr(c, 'class_body', None) or getattr(c, 'members', None)
            body_s = ""
            if cbody:
                # render inner class body members indented
                if isinstance(cbody, (list, tuple)):
                    inner = "\n".join(unparse(m, 2 + indent_level) for m in cbody)
                else:
                    inner = unparse(cbody, 2 + indent_level)
                body_s = " {\n" + inner + "\n" + IND * (indent_level + 1) + "}"
            return f"{cname}{args_s}{body_s}"

        consts_s = ", ".join(_render_enum_const(c) for c in consts) if consts else ""
        header = f"{mods}enum {name}"
        out = ind(header + " {")

        # render constants (once)
        if consts_s:
            out = out + "\n" + IND * (indent_level + 1) + consts_s + ";"

        # filter body members to exclude enum-constant nodes to avoid duplicates
        def _is_enum_constant_node(n):
            return type(n).__name__ in ('EnumConstant', 'EnumConstantDeclaration', 'EnumMember', 'EnumConstantDeclarator')

        members = []
        if body:
            if isinstance(body, (list, tuple)):
                members = [m for m in body if not _is_enum_constant_node(m)]
            else:
                members = [body] if not _is_enum_constant_node(body) else []

        if members:
            members_s = "\n".join(unparse(m, indent_level + 1) for m in members if unparse(m, indent_level + 1))
            if members_s:
                out = out + "\n" + members_s

        out = out + "\n" + ind("}")
        return out
    # TryStatement
    if node_type in ('TryStatement',):
        resources = getattr(node, 'resources', None) or getattr(node, 'resource_specification', None)
        block = getattr(node, 'block', None) or getattr(node, 'body', None)
        catches = getattr(node, 'catches', []) or getattr(node, 'catch_clauses', []) or []
        finally_block = getattr(node, 'finally_block', None) or getattr(node, 'finalizer', None)
        header = "try"
        if resources:
            try:
                # render each resource inline (avoid using LocalVariableDeclaration's trailing ';')
                def render_resource(r):
                    # common patterns: LocalVariableDeclaration / VariableDeclarator / Resource
                    if r is None:
                        return ""
                    rt = type(r).__name__
                    # If it's a simple AST node that already renders well, use unparse but strip trailing semicolon/spaces
                    if rt in ('LocalVariableDeclaration', 'VariableDeclaration'):
                        typ = getattr(r, 'type', None)
                        decls = getattr(r, 'declarators', None) or getattr(r, 'declarator', None) or []
                        if not isinstance(decls, (list, tuple)):
                            decls = [decls]
                        decl_parts = []
                        for d in decls:
                            # variable declarator may render as "name = init"
                            d_s = unparse(d)
                            decl_parts.append(d_s)
                        typ_s = unparse(typ) if typ is not None else ""
                        return f"{typ_s} " + ", ".join(decl_parts)
                    # Resource node might directly expose 'type' and 'name'
                    if hasattr(r, 'type') and (hasattr(r, 'name') or hasattr(r, 'variable')):
                        typ = getattr(r, 'type', None)
                        name = getattr(r, 'name', None) or getattr(r, 'variable', None)
                        init = getattr(r, 'initializer', None) or getattr(r, 'expression', None)
                        parts = []
                        if typ is not None:
                            parts.append(unparse(typ))
                        if name is not None:
                            parts.append(str(name))
                        if init is not None:
                            parts.append("= " + unparse(init))
                        return " ".join(parts)
                    # fallback to generic unparse (strip ';' if present)
                    rs = unparse(r)
                    rs = rs.strip()
                    if rs.endswith(';'):
                        rs = rs[:-1].strip()
                    return rs

                if isinstance(resources, (list, tuple)):
                    res_s = ", ".join(render_resource(r) for r in resources if r is not None)
                else:
                    res_s = render_resource(resources)
                header += f" ({res_s})"
            except Exception:
                pass
        out = ind(header + " {")
        if block:
            out = out + "\n" + unparse(block, indent_level + 1)
        out = out + "\n" + ind("}")
        for c in catches:
            # catch clause typically has a parameter object; render it robustly to preserve case
            param = getattr(c, 'parameter', None) or getattr(c, 'param', None) or getattr(c, 'parameter', None)
            # Debug: print param structure when debugging
            # if DEBUG_CATCH: print("DEBUG: catch param __dict__:", getattr(param, "__dict__", {}))
            param_s = ""
            if param is not None:
                # prefer explicit type + name assembly to avoid fallback lowercasing
                ptype = getattr(param, 'type', None) or getattr(param, 'types', None) or getattr(param, 'type_name', None)
                pname = getattr(param, 'name', None) or getattr(param, 'identifier', None) or ""
                try:
                    if ptype is None:
                        # fallback to best-effort unparse of whole param
                        param_s = unparse(param)
                    else:
                        if isinstance(ptype, (list, tuple)):
                            ptype_s = ", ".join(unparse(t) for t in ptype)
                        else:
                            ptype_s = unparse(ptype)
                        param_s = (ptype_s + " " + str(pname)).strip()
                except Exception:
                    param_s = unparse(param)
            cblock = getattr(c, 'block', None) or getattr(c, 'body', None)
            out = out + "\n" + ind(f"catch ({param_s}) ")
            if cblock:
                cbs = unparse(cblock, indent_level + 1)
                if cbs.strip().startswith("{"):
                    out += cbs
                else:
                    out += "{\n" + cbs + "\n" + ind("}")
            else:
                out += "{ }"
        if finally_block:
            fb = unparse(finally_block, indent_level + 1)
            out = out + "\n" + ind("finally ") + (fb if fb.strip().startswith("{") else "{\n" + fb + "\n" + ind("}"))
        return out

    # LambdaExpression
    if node_type in ('LambdaExpression',):
        params = getattr(node, 'parameters', None) or getattr(node, 'params', None) or []
        if isinstance(params, (list, tuple)):
            params_s = ", ".join(unparse(p) if not isinstance(p, str) else p for p in params)
        else:
            params_s = unparse(params) if params is not None else ""
        body = getattr(node, 'body', None) or getattr(node, 'expression', None)
        body_s = unparse(body) if body is not None else ""
        # if body is block, keep it; otherwise expression
        return f"({params_s}) -> {body_s}"
    
    # Fallback: try common attrs
    if hasattr(node, 'attrs'):
        parts = []
        for a in getattr(node, 'attrs', []):
            try:
                v = getattr(node, a)
            except Exception:
                v = None
            if v is None or v == []:
                continue
            if isinstance(v, (str, int, float)):
                parts.append(str(v))
            elif isinstance(v, (set, list, tuple)):
                # 常见情况：modifiers 可能是 set/list/tuple of str
                if all(isinstance(x, str) for x in v):
                    parts.append(_format_modifiers(v))
                else:
                    items = []
                    for x in v:
                        try:
                            items.append(unparse(x) if not isinstance(x, str) else x)
                        except Exception:
                            items.append(str(x))
                    parts.append(", ".join(items))
            else:
                parts.append(unparse(v))
        if parts:
            return ' '.join(parts)
    try:
        return str(node)
    except Exception:
        return ""   
def _is_null_check(cond):
    """
    判断 cond 是否为 x != null 或 null != x 或 x == null（可扩展）
    返回 (is_check, var_name, op)  op in {'!=','=='}
    """
    if cond is None:
        return (False, None, None)
    t = type(cond).__name__
    if t == "BinaryOperation":
        op = getattr(cond, "operator", None)
        left = getattr(cond, "operandl", None)
        right = getattr(cond, "operandr", None)
        # left or right may be MemberReference or Literal
        if _is_null_literal(left) and hasattr(right, "member"):
            return (op in ("==", "!="), getattr(right, "member", None), op)
        if _is_null_literal(right) and hasattr(left, "member"):
            return (op in ("==", "!="), getattr(left, "member", None), op)
        # also accept simple identifiers (name attr)
        if _is_null_literal(left) and hasattr(right, "name"):
            return (op in ("==", "!="), getattr(right, "name", None), op)
        if _is_null_literal(right) and hasattr(left, "name"):
            return (op in ("==", "!="), getattr(left, "name", None), op)
    return (False, None, None)
# 新增：判断表达式是否是字符串字面量（用于避免对字符串操作进行算术/逻辑运算的变异）
def _is_string_literal(node):
    """
    返回 True 如果 node 是字符串字面量（例如 Literal(value='"abc"') 或 Literal(value='\'abc\'')）
    仅对字面量做判断，避免误判其他返回字符串的方法调用等复杂场景。
    """
    if node is None:
        return False
    try:
        t = type(node).__name__
        if t == "Literal":
            v = getattr(node, "value", None)
            if not isinstance(v, str):
                return False
            s = v.strip()
            return (len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")))
        # 有时字符串可能以类型为 BasicType/ReferenceType 出现（较少见），不在此处判定
    except Exception:
        pass
    return False
def _remove_node_from_parent(node, parent):
    """从 parent 中移除 node（用于删除 if (x == null) { ... } 这种 guard 本身）。
    变更：当 node 是 IfStatement 且包含 else_statement 时，替换为 else_statement 而不是直接删除，
    以正确处理 if-else / else-if 链的语义（保留剩余分支）。
    """
    try:
        try:
            node_s = unparse(node)
        except Exception:
            node_s = None

        # helper to try removing/replacing from a statements list
        def _try_remove_from_list(stmts):
            if stmts is None:
                return False
            for idx, s in enumerate(list(stmts)):
                # direct identity (node may appear directly as a statement in some AST variants)
                if s is node:
                    # if it's an IfStatement with an else branch, replace with else branch
                    else_stmt = getattr(node, "else_statement", None)
                    if else_stmt is not None:
                        # if else is a block with statements, splice them in; otherwise insert the single else node
                        if hasattr(else_stmt, "statements") and getattr(else_stmt, "statements") is not None:
                            stmts[idx:idx+1] = list(getattr(else_stmt, "statements"))
                        else:
                            stmts[idx:idx+1] = [else_stmt]
                    else:
                        stmts[idx:idx+1] = []
                    return True

                # common wrappers: check both 'statement' and 'expression' fields
                try:
                    if hasattr(s, "statement") and getattr(s, "statement") is node:
                        else_stmt = getattr(node, "else_statement", None)
                        if else_stmt is not None:
                            if hasattr(else_stmt, "statements") and getattr(else_stmt, "statements") is not None:
                                stmts[idx:idx+1] = list(getattr(else_stmt, "statements"))
                            else:
                                stmts[idx:idx+1] = [else_stmt]
                        else:
                            stmts[idx:idx+1] = []
                        return True
                except Exception:
                    pass
                try:
                    if hasattr(s, "expression") and getattr(s, "expression") is node:
                        else_stmt = getattr(node, "else_statement", None)
                        if else_stmt is not None:
                            if hasattr(else_stmt, "statements") and getattr(else_stmt, "statements") is not None:
                                stmts[idx:idx+1] = list(getattr(else_stmt, "statements"))
                            else:
                                stmts[idx:idx+1] = [else_stmt]
                        else:
                            stmts[idx:idx+1] = []
                        return True
                except Exception:
                    pass

                # textual fallback: compare rendered source of the statement with target node
                if node_s is not None:
                    try:
                        s_s = unparse(s)
                        if s_s == node_s or node_s in s_s or s_s in (node_s or ""):
                            else_stmt = getattr(node, "else_statement", None)
                            if else_stmt is not None:
                                if hasattr(else_stmt, "statements") and getattr(else_stmt, "statements") is not None:
                                    stmts[idx:idx+1] = list(getattr(else_stmt, "statements"))
                                else:
                                    stmts[idx:idx+1] = [else_stmt]
                            else:
                                stmts[idx:idx+1] = []
                            return True
                    except Exception:
                        pass
            return False

        if isinstance(parent, list):
            if _try_remove_from_list(parent):
                return True

        # Case A: parent directly exposes statements
        if hasattr(parent, "statements") and getattr(parent, "statements") is not None:
            if _try_remove_from_list(parent.statements):
                return True

        # Case B: parent has .statement single-slot (BlockStatement wrapper etc.)
        if hasattr(parent, "statement"):
            try:
                ps = getattr(parent, "statement")
                # if parent.statement directly is the node -> replace with else or None
                if ps is node:
                    else_stmt = getattr(node, "else_statement", None)
                    if else_stmt is not None:
                        if hasattr(else_stmt, "statements") and getattr(else_stmt, "statements") is not None:
                            # choose a single Block-like or first statement as appropriate
                            if len(getattr(else_stmt, "statements")) == 1:
                                parent.statement = list(getattr(else_stmt, "statements"))[0]
                            else:
                                ns = types.SimpleNamespace()
                                ns.statements = list(getattr(else_stmt, "statements"))
                                parent.statement = ns
                        else:
                            parent.statement = else_stmt
                    else:
                        parent.statement = None
                    return True
                # if statement itself is a Block-like with statements
                if hasattr(ps, "statements") and isinstance(getattr(ps, "statements"), (list, tuple)):
                    if _try_remove_from_list(ps.statements):
                        return True
                # fallback textual match
                if node_s is not None:
                    try:
                        ps_s = unparse(ps)
                        if ps_s == node_s or node_s in ps_s:
                            else_stmt = getattr(node, "else_statement", None)
                            if else_stmt is not None:
                                if hasattr(else_stmt, "statements") and getattr(else_stmt, "statements") is not None:
                                    if len(getattr(else_stmt, "statements")) == 1:
                                        parent.statement = list(getattr(else_stmt, "statements"))[0]
                                    else:
                                        ns = types.SimpleNamespace()
                                        ns.statements = list(getattr(else_stmt, "statements"))
                                        parent.statement = ns
                                else:
                                    parent.statement = else_stmt
                            else:
                                parent.statement = None
                            return True
                    except Exception:
                        pass
            except Exception:
                pass

        # Case C: parent is MethodDeclaration with .body (Block)
        if hasattr(parent, "body") and getattr(parent, "body") is not None:
            try:
                body = getattr(parent, "body")
                if hasattr(body, "statements") and body.statements is not None:
                    if _try_remove_from_list(body.statements):
                        return True
                # also try if parent.body itself equals the node (rare)
                if body is node:
                    else_stmt = getattr(node, "else_statement", None)
                    try:
                        if else_stmt is not None:
                            setattr(parent, "body", else_stmt)
                        else:
                            parent.body = None
                        return True
                    except Exception:
                        pass
            except Exception:
                pass

        # Case D: parent has .block (TryStatement, CatchClause etc.)
        if hasattr(parent, "block") and getattr(parent, "block") is not None:
            try:
                blk = getattr(parent, "block")
                if hasattr(blk, "statements") and blk.statements is not None:
                    if _try_remove_from_list(blk.statements):
                        return True
                if blk is node:
                    else_stmt = getattr(node, "else_statement", None)
                    try:
                        if else_stmt is not None:
                            setattr(parent, "block", else_stmt)
                        else:
                            parent.block = None
                        return True
                    except Exception:
                        pass
            except Exception:
                pass

        # Case E: some nodes expose finally_block / finally
        for attr in ("finally_block", "finally", "finalizer"):
            if hasattr(parent, attr) and getattr(parent, attr) is not None:
                fb = getattr(parent, attr)
                try:
                    if hasattr(fb, "statements") and fb.statements is not None:
                        if _try_remove_from_list(fb.statements):
                            return True
                    if fb is node:
                        else_stmt = getattr(node, "else_statement", None)
                        try:
                            if else_stmt is not None:
                                setattr(parent, attr, else_stmt)
                            else:
                                setattr(parent, attr, None)
                            return True
                        except Exception:
                            pass
                except Exception:
                    pass

        # Last resort: try attributes listed in parent.attrs and do textual replace
        attrs = getattr(parent, "attrs", []) or []
        for a in attrs:
            try:
                v = getattr(parent, a)
            except Exception:
                v = None
            if v is None:
                continue
            # list attr
            if isinstance(v, (list, tuple)):
                for idx, it in enumerate(list(v)):
                    if it is node:
                        try:
                            lv = list(v)
                            else_stmt = getattr(node, "else_statement", None)
                            if else_stmt is not None:
                                if hasattr(else_stmt, "statements") and getattr(else_stmt, "statements") is not None:
                                    lv[idx:idx+1] = list(getattr(else_stmt, "statements"))
                                else:
                                    lv[idx:idx+1] = [else_stmt]
                            else:
                                lv[idx:idx+1] = []
                            setattr(parent, a, lv)
                            return True
                        except Exception:
                            pass
                    if node_s is not None:
                        try:
                            if unparse(it) == node_s or node_s in unparse(it):
                                lv = list(v)
                                else_stmt = getattr(node, "else_statement", None)
                                if else_stmt is not None:
                                    if hasattr(else_stmt, "statements") and getattr(else_stmt, "statements") is not None:
                                        lv[idx:idx+1] = list(getattr(else_stmt, "statements"))
                                    else:
                                        lv[idx:idx+1] = [else_stmt]
                                else:
                                    lv[idx:idx+1] = []
                                setattr(parent, a, lv)
                                return True
                        except Exception:
                            pass
            else:
                if v is node:
                    try:
                        else_stmt = getattr(node, "else_statement", None)
                        if else_stmt is not None:
                            setattr(parent, a, else_stmt)
                        else:
                            setattr(parent, a, None)
                        return True
                    except Exception:
                        pass
                if node_s is not None:
                    try:
                        if unparse(v) == node_s or node_s in unparse(v):
                            try:
                                else_stmt = getattr(node, "else_statement", None)
                                if else_stmt is not None:
                                    setattr(parent, a, else_stmt)
                                else:
                                    setattr(parent, a, None)
                                return True
                            except Exception:
                                pass
                    except Exception:
                        pass
    except Exception:
        pass
    return False
def _is_var_dereferenced_in_method(method_node, var_name):
    """返回 True 如果方法中存在对 var_name 的解引用（成员访问或方法调用 qualifier == var_name）。"""
    if var_name is None:
        return False
    try:
        # MemberReference qualifiers like "obj.field" -> qualifier == obj
        for _, mr in method_node.filter(_jtree.MemberReference):
            try:
                q = getattr(mr, "qualifier", None)
                if q is not None and unparse(q).strip() == var_name:
                    return True
            except Exception:
                pass
        # MethodInvocation qualifier: obj.method(...)
        for _, mi in method_node.filter(_jtree.MethodInvocation):
            try:
                q = getattr(mi, "qualifier", None)
                if q is not None and unparse(q).strip() == var_name:
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False
def _node_has_dereference(node, var):
    """返回 True 如果 node（或其子节点）包含对 var 的解引用（qualifier == var 或 method qualifier == var）。"""
    if node is None or var is None:
        return False
    try:
        for _, mr in node.filter(_jtree.MemberReference):
            q = getattr(mr, "qualifier", None)
            if q is not None and unparse(q).strip() == var:
                return True
        for _, mi in node.filter(_jtree.MethodInvocation):
            q = getattr(mi, "qualifier", None)
            if q is not None and unparse(q).strip() == var:
                return True
    except Exception:
        pass
    return False
class BugInject:
    @staticmethod
    def nullinject(target_method_node):
        """
        优化版 nullinject：
        - 保留及优化原有逻辑 (IfStatement check removal/unwrap, VariableDeclarator new->null)
        - 新增: Assignment new->null (e.g. x = new T() -> x = null)
        - 新增: Remove Objects.requireNonNull(x) checks
        """
        print("正在植入 Bug: Null Reference Failures")
        result = []
        vid = 0

        # helpers: detect early-exit or local-declarations inside a node (unsafe to unwrap)
        def _has_early_exit_or_declaration(node):
            if node is None:
                return False
            try:
                # fix tuple filter
                if any(node.filter(ReturnStatement)) or \
                   any(node.filter(_jtree.ThrowStatement)) or \
                   any(node.filter(_jtree.BreakStatement)) or \
                   any(node.filter(_jtree.ContinueStatement)) or \
                   any(node.filter(LocalVariableDeclaration)):
                    return True
            except Exception:
                pass
            return False

        # collect null-check candidates (condition text, var, op)
        null_check_conds = []
        for path, if_node in target_method_node.filter(IfStatement):
            cond = getattr(if_node, "condition", None)
            ok, var_name, op = _is_null_check(cond)
            if not ok:
                continue
            try:
                cond_s = unparse(cond)
            except Exception:
                cond_s = None
            null_check_conds.append((cond_s, var_name, op))

        # 1. IF Checks Mutations
        for cond_s, var_name, op in null_check_conds:
            # fresh copy per candidate
            copy_method = copy.deepcopy(target_method_node)
            produced_any = False

            for path2, if_node2 in copy_method.filter(IfStatement):
                cond2 = getattr(if_node2, "condition", None)
                try:
                    cond2_s = unparse(cond2)
                except Exception:
                    cond2_s = None
                ok2, var2, op2 = _is_null_check(cond2)
                if not ok2:
                    continue

                # match by variable+op or by condition text
                same = False
                if var_name and var2 and var_name == var2 and op2 == op:
                    same = True
                elif cond_s is None or cond2_s == cond_s:
                    same = True
                if not same:
                    continue

                # locate a parent container for AST-level replace/remove
                parent = None
                for anc in reversed(path2[:-1]):
                    if hasattr(anc, "statements") or hasattr(anc, "statement"):
                        parent = anc
                        break
                if parent is None:
                    parent = path2[-2] if len(path2) >= 2 else None
                if parent is None:
                    continue

                then_stmt = getattr(if_node2, "then_statement", None)
                else_stmt = getattr(if_node2, "else_statement", None)

                # deref checks
                deref_then = _node_has_dereference(then_stmt, var2)
                deref_else = _node_has_dereference(else_stmt, var2)
                deref_anywhere = _is_var_dereferenced_in_method(copy_method, var2)
                used_after = _is_var_used_after_in_method(copy_method, path2, if_node2, var2)

                # CASE: if (x != null) -> unwrap then
                if op2 == "!=":
                    # safety: do not unwrap if then contains early-exit or local-declarations (may change scope/flow)
                    if _has_early_exit_or_declaration(then_stmt):
                        continue
                    # allow unwrap only when it meaningfully exposes deref risk:
                    # - then contains deref, or method elsewhere derefs var AFTER this if, or used-after heuristic
                    allow_unwrap = deref_then or (deref_anywhere and used_after) or used_after or deref_else or True
                    if not allow_unwrap:
                        continue

                    ok_unwrap = _unwrap_if_statement_in_parent(if_node2, parent)
                    if not ok_unwrap:
                        continue

                    # after unwrap, re-evaluate whether method now contains deref (i.e., we likely created NPE risk)
                    is_deref = _is_var_dereferenced_in_method(copy_method, var2)
                    if is_deref:
                        btype = "Null Reference Failures"
                        mut = "unwrap_not_null_guard"
                    else:
                        btype = "Incorrect Behavior Failures"
                        mut = "unwrap_not_null_guard_as_logic"

                    result.append({
                        "bug_id": vid,
                        "bug_type": btype,
                        "mutation": mut,
                        "removed_condition": cond2_s,
                        "code": unparse(copy_method)
                    })
                    vid += 1
                    produced_any = True
                    break

                # CASE: if (x == null) -> remove guard (replace with else when present)
                if op2 == "==":
                    # do not remove if then contains declarations/early-exit that changing would be unsafe
                    if _has_early_exit_or_declaration(getattr(if_node2, "then_statement", None)):
                        # removal would skip the then which may have return/throw - unsafe
                        continue
                    # allow removal if else or later code dereferences var or used-after heuristic
                    allow_remove = deref_else or (deref_anywhere and used_after) or used_after or True
                    # additional relaxed condition: if else uses var and does not early-exit, allow
                    if not allow_remove and else_stmt is not None:
                        try:
                            else_src = unparse(else_stmt) or ""
                        except Exception:
                            else_src = ""
                        if else_src and re.search(r'\b' + re.escape(var2) + r'\b', else_src) and not re.search(r'\b(return|throw|break|continue)\b', else_src):
                            allow_remove = True

                    if not allow_remove:
                        continue

                    ok_rem = _remove_node_from_parent(if_node2, parent)
                    if not ok_rem:
                        continue

                    is_deref = _is_var_dereferenced_in_method(copy_method, var2)
                    if is_deref:
                        btype = "Null Reference Failures"
                        mut = "remove_null_check_equals"
                    else:
                        btype = "Incorrect Behavior Failures"
                        mut = "remove_null_check_equals_as_logic"

                    result.append({
                        "bug_id": vid,
                        "bug_type": btype,
                        "mutation": mut,
                        "removed_condition": cond2_s,
                        "code": unparse(copy_method)
                    })
                    vid += 1
                    produced_any = True
                    break

            # if not produced for this candidate, continue to next candidate
            if not produced_any:
                continue

        # 2. VariableDeclarator (T x = new ...) -> null
        for path, var_decl in target_method_node.filter(VariableDeclarator):
            init = getattr(var_decl, "initializer", None)
            if init is None:
                continue
            tname = type(init).__name__
            is_creator_like = ("Creator" in tname) 
            if not is_creator_like:
                try:
                    is_creator_like = bool(unparse(init).strip().startswith("new "))
                except Exception:
                    is_creator_like = False
            if not is_creator_like:
                continue

            try:
                init_s = unparse(init)
            except Exception:
                init_s = None
            var_name = getattr(var_decl, "name", None)

            copy_method = copy.deepcopy(target_method_node)
            applied = False
            for p2, vd2 in copy_method.filter(VariableDeclarator):
                try:
                    if getattr(vd2, "name", None) != var_name:
                        continue
                    in2 = getattr(vd2, "initializer", None)
                    try:
                        in2_s = unparse(in2)
                    except Exception:
                        in2_s = None
                    if init_s is not None and in2_s != init_s:
                        continue
                    vd2.initializer = Literal(value="null")
                    result.append({
                        "bug_id": vid,
                        "bug_type": "Null Reference Failures",
                        "mutation": "new_to_null_decl",
                        "original_new": init_s,
                        "code": unparse(copy_method)
                    })
                    vid += 1
                    applied = True
                    break
                except Exception:
                    continue
            if applied:
                continue

        # 3. Assignment (x = new ...) -> null
        all_assigns = list(target_method_node.filter(Assignment))
        candidates_idx = []
        for i, (path, assign) in enumerate(all_assigns):
            val = getattr(assign, "value", None)
            if val is None: continue
            
            # Check Creator / "new "
            tname = type(val).__name__
            is_creator_like = ("Creator" in tname)
            if not is_creator_like:
                try:
                    is_creator_like = bool(unparse(val).strip().startswith("new "))
                except Exception:
                    is_creator_like = False
            if is_creator_like:
                candidates_idx.append(i)

        for idx in candidates_idx:
            copy_method = copy.deepcopy(target_method_node)
            copy_assigns = list(copy_method.filter(Assignment))
            
            if idx < len(copy_assigns):
                path2, assign2 = copy_assigns[idx]
                try:
                    orig_val_s = unparse(assign2.value)
                except: 
                    orig_val_s = "new ..."
                
                assign2.value = Literal(value="null")
                result.append({
                    "bug_id": vid,
                    "bug_type": "Null Reference Failures",
                    "mutation": "new_to_null_assign",
                    "original_new": orig_val_s,
                    "code": unparse(copy_method)
                })
                vid += 1

        # 4. Objects.requireNonNull(x) removal
        all_mis = list(target_method_node.filter(_jtree.MethodInvocation))
        req_candidates = []
        for i, (path, mi) in enumerate(all_mis):
            if getattr(mi, "member", None) == "requireNonNull":
                # Ensure it has arguments (at least 1)
                args = getattr(mi, "arguments", []) or []
                if args:
                    req_candidates.append(i)
        
        for idx in req_candidates:
            copy_method = copy.deepcopy(target_method_node)
            applied = False
            
            # Find the i-th method invocation in copy
            copy_mis = list(copy_method.filter(_jtree.MethodInvocation))
            if idx < len(copy_mis):
                path2, mi2 = copy_mis[idx]
                arg0 = mi2.arguments[0] if mi2.arguments else None
                if arg0:
                    parent = path2[-1] if len(path2) >= 1 else None
                    if parent:
                        # Case A: StatementExpression (effectively a statement "Objects.requireNonNull(x);")
                        if type(parent).__name__ == "StatementExpression":
                            grandparent = path2[-2] if len(path2) >= 2 else None
                            if grandparent:
                                # If grandparent is a list (statements list), remove directly
                                if isinstance(grandparent, list):
                                    try:
                                        grandparent.remove(parent)
                                        applied = True
                                    except ValueError:
                                        pass
                                # Check if grandparent is a Node that we can use helper on (fallback)
                                elif not isinstance(grandparent, (list, tuple)):
                                    if _remove_node_from_parent(parent, grandparent):
                                        applied = True
                        
                        # Case B: Expression (x = Objects.requireNonNull(y)) or fallback replacer
                        if not applied:
                            # Replace mi2 with arg0 in parent attributes
                            replaced_in_parent = False
                            if hasattr(parent, "attrs") and parent.attrs: 
                                for attr in parent.attrs:
                                    val = getattr(parent, attr, None)
                                    if val is mi2:
                                        setattr(parent, attr, arg0)
                                        replaced_in_parent = True
                                        break
                                    elif isinstance(val, list):
                                        for list_idx, item in enumerate(val):
                                            if item is mi2:
                                                val[list_idx] = arg0
                                                replaced_in_parent = True
                                                break
                                        if replaced_in_parent: break
                            if replaced_in_parent:
                                applied = True
            
            if applied:
                result.append({
                    "bug_id": vid,
                    "bug_type": "Null Reference Failures",
                    "mutation": "remove_requireNonNull",
                    "code": unparse(copy_method)
                })
                vid += 1
        # # also keep existing qualifier-to-null method-invocation mutations (unchanged)
        # all_mis = list(target_method_node.filter(_jtree.MethodInvocation))
        # for idx, (path, mi) in enumerate(all_mis):
        #     qual = getattr(mi, "qualifier", None)
        #     if not qual:
        #         continue
        #     try:
        #         qual_s = unparse(qual)
        #     except Exception:
        #         qual_s = None
        #     member_name = getattr(mi, "member", None)

        #     same_ord = 0
        #     for _, mprev in all_mis[:idx]:
        #         try:
        #             if getattr(mprev, "member", None) == member_name and unparse(getattr(mprev, "qualifier", None)) == qual_s:
        #                 same_ord += 1
        #         except Exception:
        #             continue

        #     copy_method = copy.deepcopy(target_method_node)
        #     modified = False
        #     occ = 0
        #     for p2, mi2 in copy_method.filter(_jtree.MethodInvocation):
        #         try:
        #             mi2_qual_s = unparse(getattr(mi2, "qualifier", None))
        #         except Exception:
        #             mi2_qual_s = None
        #         if getattr(mi2, "member", None) == member_name and mi2_qual_s == qual_s:
        #             if occ == same_ord:
        #                 try:
        #                     mi2.qualifier = Literal(value="null")
        #                     result.append({
        #                         "bug_id": vid,
        #                         "bug_type": "Null Reference Failures",
        #                         "mutation": "qualifier_to_null",
        #                         "member": getattr(mi2, "member", None),
        #                         "code": unparse(copy_method)
        #                     })
        #                     vid += 1
        #                     modified = True
        #                 except Exception:
        #                     pass
        #                 break
        #             occ += 1
        #     if modified:
        #         continue
        return result
    def Indexinject(target_method_node):
        # 植入bug 2： Index Boundary Failures 数组访问缺少边界检查，List.get() 缺少边界检查，以及添加+1 -1边界
        print("正在植入 Bug: Index Boundary Failures")
        result = []
        id = 0
        def _is_length_like(node):
            if node is None:
                return False
            t = type(node).__name__
            # arr.length (MemberReference) 或 list.size()/list.length() (MethodInvocation)
            if t == "MemberReference" and getattr(node, "member", None) == "length":
                return True
            if t == "MethodInvocation" and getattr(node, "member", None) in ("size", "length"):
                return True
            return False

        def _is_index_boundary_check(cond):
            """
            识别形如 i < arr.length, i <= arr.length-1, arr.length > i, i >= arr.length 等 guard。
            返回 (is_check, index_node, operator, collection_length_string)
            """
            if cond is None or type(cond).__name__ != "BinaryOperation":
                return (False, None, None, None)
            op = getattr(cond, "operator", None)
            left = getattr(cond, "operandl", None)
            right = getattr(cond, "operandr", None)
            try:
                # index < coll.length  或 index <= coll.length - 1
                if _is_length_like(right):
                    return (op in ("<", "<=", ">", ">="), left, op, unparse(right))
                # coll.length > index (reverse)
                if _is_length_like(left):
                    return (op in ("<", "<=", ">", ">="), right, op, unparse(left))
            except Exception:
                pass
            return (False, None, None, None)
        
        # Helper: try to create new BinaryOperation index +/- 1
        def _make_offset_index(orig_expr, sign):
            try:
                return BinaryOperation(operator=sign, operandl=orig_expr, operandr=Literal(value="1"))
            except Exception:
                return None

        # 1) 变异边界检查 guard
        # 使用绝对索引定位，避免重复变异
        all_ifs_orig = list(target_method_node.filter(IfStatement))
        for idx, (path, if_node) in enumerate(all_ifs_orig):
            cond = getattr(if_node, "condition", None)
            is_chk, idx_node, op, coll_s = _is_index_boundary_check(cond)
            if not is_chk:
                continue
            
            try:
                cond_s = unparse(cond)
            except:
                cond_s = "condition"

            # 1a: Remove guard (unwrap)
            copy_method = copy.deepcopy(target_method_node)
            try:
                copy_all_ifs = list(copy_method.filter(IfStatement))
                if idx < len(copy_all_ifs):
                    path2, if_node2 = copy_all_ifs[idx]
                    
                    # Find parent to unwrap
                    parent = None
                    # path2 contains root...node. Parent is second to last.
                    # Or safer: search specific containers in path
                    if len(path2) >= 1:
                        for anc in reversed(path2[:-1]):
                            if hasattr(anc, "statements") or hasattr(anc, "statement"):
                                parent = anc
                                break
                    # Fallback check
                    if parent is None and len(path2) >= 2:
                        parent = path2[-2]

                    if parent and _unwrap_if_statement_in_parent(if_node2, parent):
                        result.append({
                            "bug_id": id,
                            "bug_type": "Index Boundary Failures",
                            "mutation": "remove_index_guard",
                            "removed_condition": cond_s,
                            "code": unparse(copy_method)
                        })
                        id += 1
            except Exception:
                pass

            # 1b: Weaken guard
            weakened_op = None
            if op == "<": weakened_op = "<="
            elif op == ">": weakened_op = ">="
            
            if weakened_op:
                copy_method_w = copy.deepcopy(target_method_node)
                try:
                    copy_all_ifs = list(copy_method_w.filter(IfStatement))
                    if idx < len(copy_all_ifs):
                        path2, if_node2 = copy_all_ifs[idx]
                        cond2 = if_node2.condition
                        if hasattr(cond2, "operator"):
                            cond2.operator = weakened_op
                            result.append({
                                "bug_id": id,
                                "bug_type": "Index Boundary Failures",
                                "mutation": "weaken_index_guard",
                                "original_condition": cond_s,
                                "new_condition": unparse(cond2),
                                "code": unparse(copy_method_w)
                            })
                            id += 1
                except Exception:
                    pass

        # 新增：将 for/control 中的松/严格边界（<= -> <, >= -> >）变为更易触发越界的形式
        all_fors_orig = list(target_method_node.filter(ForStatement))
        for idx, (path, for_node) in enumerate(all_fors_orig):
            control = getattr(for_node, "control", None)
            if control is None: continue
            cond = getattr(control, "condition", None) or getattr(for_node, "condition", None)
            if cond is None or type(cond).__name__ != "BinaryOperation": continue
            op = getattr(cond, "operator", None)
            if op not in ("<=", ">=", "<", ">"): continue
            
            left, right = getattr(cond, "operandl", None), getattr(cond, "operandr", None)
            try:
                if not (_is_length_like(right) or _is_length_like(left)): continue
            except: continue
            
            new_op = None
            if op == "<=": new_op = "<"
            elif op == ">=": new_op = ">"
            elif op == "<": new_op = "<="
            elif op == ">": new_op = ">="
            
            if new_op:
                copy_method = copy.deepcopy(target_method_node)
                try:
                    copy_all_fors = list(copy_method.filter(ForStatement))
                    if idx < len(copy_all_fors):
                        path2, for_node2 = copy_all_fors[idx]
                        ctrl2 = getattr(for_node2, "control", None)
                        cond2 = getattr(ctrl2, "condition", None) or getattr(for_node2, "condition", None)
                        if cond2 and hasattr(cond2, "operator"):
                            cond2.operator = new_op
                            result.append({
                                "bug_id": id,
                                "bug_type": "Index Boundary Failures",
                                "mutation": "loop_bound_strictness_change",
                                "original_condition": unparse(cond),
                                "new_condition": unparse(cond2),
                                "code": unparse(copy_method)
                            })
                            id += 1
                except Exception: pass

        # 2) 在访问点对索引进行 +/-1 变异
        # a) 数组下标访问 ArraySelector/ArrayAccess
        all_arrs_orig = list(target_method_node.filter(_jtree.ArraySelector))
        for idx, (path, arr_node) in enumerate(all_arrs_orig):
            # Try to render original index for description
            try:
                orig_idx_s = unparse(getattr(arr_node, "index", None) or getattr(arr_node, "selector", None))
            except:
                orig_idx_s = "index"

            for sign, mut_name in (("+", "index_plus_one"), ("-", "index_minus_one")):
                copy_method = copy.deepcopy(target_method_node)
                try:
                    copy_all_arrs = list(copy_method.filter(_jtree.ArraySelector))
                    if idx < len(copy_all_arrs):
                        path2, arr_node2 = copy_all_arrs[idx]
                        idx_attr = "index" if hasattr(arr_node2, "index") else "selector"
                        curr_idx = getattr(arr_node2, idx_attr, None)
                        if curr_idx:
                            new_idx = _make_offset_index(curr_idx, sign)
                            if new_idx:
                                setattr(arr_node2, idx_attr, new_idx)
                                result.append({
                                    "bug_id": id,
                                    "bug_type": "Index Boundary Failures",
                                    "mutation": mut_name,
                                    "original_index": orig_idx_s,
                                    "code": unparse(copy_method)
                                })
                                id += 1
                except Exception: pass

        # b) MethodInvocation arguments mutation (+1 / -1)
        target_members_1arg = ("get", "charAt", "remove", "set", "add", "substring") # index is arg0
        target_members_2args = ("substring", "subList") # index is arg0 AND arg1
        
        all_mis_orig = list(target_method_node.filter(_jtree.MethodInvocation))
        for idx, (path, mi) in enumerate(all_mis_orig):
            member = getattr(mi, "member", None)
            if member not in target_members_1arg and member not in target_members_2args:
                continue
            args = getattr(mi, "arguments", []) or []
            if not args: continue
            
            # Helper to generate mutations for arg_i
            def _mutate_arg_at(arg_i, desc_suffix):
                nonlocal id
                target_args = getattr(mi, "arguments", [])
                if arg_i >= len(target_args): return
                orig_arg_s = "arg"
                try: orig_arg_s = unparse(target_args[arg_i])
                except: pass

                for sign, s_name in (("+", "plus_one"), ("-", "minus_one")):
                    copy_method = copy.deepcopy(target_method_node)
                    try:
                        copy_mis = list(copy_method.filter(_jtree.MethodInvocation))
                        if idx < len(copy_mis):
                            path2, mi2 = copy_mis[idx]
                            args2 = getattr(mi2, "arguments", [])
                            if len(args2) > arg_i:
                                new_arg = _make_offset_index(args2[arg_i], sign)
                                if new_arg:
                                    args2[arg_i] = new_arg
                                    result.append({
                                        "bug_id": id,
                                        "bug_type": "Index Boundary Failures",
                                        "mutation": f"{member}_arg{arg_i}_{s_name}",
                                        "original_arg": orig_arg_s,
                                        "code": unparse(copy_method)
                                    })
                                    id += 1
                    except Exception: pass

            _mutate_arg_at(0, "index")
            if member in target_members_2args:
                _mutate_arg_at(1, "index")

        #c) 数组创建（new T[expr]）维度变异
        all_acs_orig = list(target_method_node.filter(ArrayCreator))
        for idx, (path, ac) in enumerate(all_acs_orig):
            dims = getattr(ac, "dimensions", None) or getattr(ac, "dimensionsExpression", None)
            if not dims:
                # fallbacks
                dims = getattr(ac, "dimensions", None)
            if not dims: continue
            
            # javalang dimensions can be a list or single node
            is_list = isinstance(dims, (list, tuple))
            dims_list = list(dims) if is_list else [dims]

            for dim_i, dim_expr in enumerate(dims_list):
                try: dim_s = unparse(dim_expr)
                except: dim_s = "dim"
                
                for sign, mut_name in (("+", "array_dim_plus_one"), ("-", "array_dim_minus_one")):
                    copy_method = copy.deepcopy(target_method_node)
                    try:
                        copy_acs = list(copy_method.filter(ArrayCreator))
                        if idx < len(copy_acs):
                            path2, ac2 = copy_acs[idx]
                            dims2 = getattr(ac2, "dimensions", None) or getattr(ac2, "dimensionsExpression", None)
                            if not dims2: dims2 = getattr(ac2, "dimensions", None)
                            
                            is_list2 = isinstance(dims2, (list, tuple))
                            dims2_list = list(dims2) if is_list2 else [dims2]
                            
                            if dim_i < len(dims2_list):
                                new_dim = _make_offset_index(dims2_list[dim_i], sign)
                                if new_dim:
                                    if is_list2:
                                        dims2_list[dim_i] = new_dim
                                        # Write back list or tuple
                                        if hasattr(ac2, "dimensions"): ac2.dimensions = dims2_list
                                        elif hasattr(ac2, "dimensionsExpression"): ac2.dimensionsExpression = dims2_list
                                    else:
                                        # Only one dim, replace it directly
                                        if hasattr(ac2, "dimensions"): ac2.dimensions = new_dim
                                        elif hasattr(ac2, "dimensionsExpression"): ac2.dimensionsExpression = new_dim
                                    
                                    result.append({
                                        "bug_id": id,
                                        "bug_type": "Index Boundary Failures",
                                        "mutation": mut_name,
                                        "original_dim": dim_s,
                                        "code": unparse(copy_method)
                                    })
                                    id += 1
                    except Exception: pass
        return result
    def resouceinject(target_method_node):
        # 植入bug 3： Resource Management Failures 资源未正确关闭，缺少 try-finally
        print("正在植入 Bug: Resource Management Failures")
        result = []
        id = 0

        # Helper: Try to unwrap resources from TWR header to block body
        all_tries_orig = list(target_method_node.filter(TryStatement))
        for idx, (path, try_node) in enumerate(all_tries_orig):
            # 1B) Disable Try-With-Resources (Memory Leak)
            # print(f"Node resources: {getattr(try_node, 'resources', None)}")
            if getattr(try_node, "resources", None):
                copy_method = copy.deepcopy(target_method_node)
                # try:
                copy_all = list(copy_method.filter(TryStatement))
                if idx < len(copy_all):
                    path2, try2 = copy_all[idx]
                    resources = getattr(try2, "resources", [])
                    if resources:
                        new_stmts = []
                        for res in resources:
                            # Use _jtree.* to be safe
                            vd = _jtree.VariableDeclarator(name=res.name)
                            if hasattr(res, "value"): vd.initializer = res.value
                            
                            lvd = _jtree.LocalVariableDeclaration(
                                modifiers=getattr(res, "modifiers", set()),
                                annotations=getattr(res, "annotations", []),
                                type=getattr(res, "type", None),
                                declarators=[vd]
                            )
                            new_stmts.append(lvd)
                        
                        # Move to block
                        if not try2.block:
                             # If missing, create empty list
                             try2.block = []
                        
                        blk = try2.block
                        
                        if isinstance(blk, list):
                             # Direct list of statements
                             blk[0:0] = new_stmts
                        else:
                             if not hasattr(blk, "statements") or blk.statements is None:
                                 blk.statements = []
                             blk.statements[0:0] = new_stmts
                        # Clear resources
                        try2.resources = []
                        
                        result.append({
                            "bug_id": id,
                            "bug_type": "Resource Management Failures",
                            "mutation": "disable_try_with_resources",
                            "code": unparse(copy_method)
                        })
                        id += 1
                # except Exception: pass

            # 1A) Remove finally block
            if getattr(try_node, "finally_block", None):
                copy_method = copy.deepcopy(target_method_node)
                # try:
                copy_all = list(copy_method.filter(TryStatement))
                if idx < len(copy_all):
                    path2, try2 = copy_all[idx]
                    if getattr(try2, "finally_block", None):
                        try2.finally_block = None
                        result.append({
                            "bug_id": id,
                            "bug_type": "Resource Management Failures",
                            "mutation": "remove_finally",
                            # "code": unparse(copy_method)
                            "code": unparse(copy_method)
                        })
                        id += 1
                # except Exception: pass

        # 2) 删除 resource.close() 调用 -> 资源泄漏
        all_mis = list(target_method_node.filter(_jtree.MethodInvocation))
        close_indices = [i for i, (p, m) in enumerate(all_mis) if getattr(m, "member", None) == "close"]
        
        for original_idx in close_indices:
            path, mi = all_mis[original_idx]
            try: qual_s = unparse(mi.qualifier)
            except: qual_s = "resource"
            
            copy_method = copy.deepcopy(target_method_node)
            try:
                copy_mis = list(copy_method.filter(_jtree.MethodInvocation))
                if original_idx < len(copy_mis):
                    path2, mi2 = copy_mis[original_idx]
                    
                    # Find parent to remove statement
                    parent = None
                    if len(path2) >= 1:
                        for anc in reversed(path2[:-1]):
                            if hasattr(anc, "statements") or hasattr(anc, "statement"):
                                parent = anc
                                break
                    if parent is None and len(path2) >= 2:
                        parent = path2[-2]
                        
                    if parent:
                        if _remove_node_from_parent(mi2, parent):
                             result.append({
                                "bug_id": id,
                                "bug_type": "Resource Management Failures",
                                "mutation": "remove_close_call",
                                "qualifier": qual_s,
                                "code": unparse(copy_method)
                            })
                             id += 1
            except Exception: pass

        # 3) 吞掉 catch 块内容 & 泛化异常
        # Use javalang.tree.CatchClause explicitly if _jtree is avail
        CatchClause = getattr(_jtree, "CatchClause", None)
        if CatchClause:
            all_catches = list(target_method_node.filter(CatchClause))
            # print(f"DEBUG: Found {len(all_catches)} catch clauses")
            for idx, (path, catch_node) in enumerate(all_catches):
                param = getattr(catch_node, "parameter", None)
                p_name = getattr(param, "name", "e") if param else "e"
                
                # Mutation A: Swallow Exception (empty block)
                copy_method = copy.deepcopy(target_method_node)
                try:
                    copy_catches = list(copy_method.filter(CatchClause))
                    if idx < len(copy_catches):
                        path2, catch2 = copy_catches[idx]
                        
                        # Ensure block exists and is empty
                        if not hasattr(catch2, "block") or catch2.block is None:
                             catch2.block = []
                        elif isinstance(catch2.block, list):
                             catch2.block = []
                        else:
                             # Emptying statements
                             catch2.block.statements = []
                        
                        result.append({
                            "bug_id": id,
                            "bug_type": "Resource Management Failures",
                            "mutation": "swallow_exception_in_catch",
                            "param_name": p_name,
                            "code": unparse(copy_method)
                        })
                        id += 1
                except Exception: pass

                # Mutation B: Replace Exception Type with Exception
                try:
                    ptype = getattr(param, "type", None)
                    ptype_name = getattr(ptype, "name", "")
                    if ptype_name and ptype_name not in ("Exception", "Throwable", "Error"):
                         copy_method2 = copy.deepcopy(target_method_node)
                         copy_catches2 = list(copy_method2.filter(CatchClause))
                         if idx < len(copy_catches2):
                             path2, catch2 = copy_catches2[idx]
                             param2 = getattr(catch2, "parameter", None)
                             type2 = getattr(param2, "type", None)
                             if type2:
                                 original_types = getattr(type2, "name", "")
                                 type2.name = "Exception"
                                 result.append({
                                    "bug_id": id,
                                    "bug_type": "Resource Management Failures",
                                    "mutation": "replace_exception_in_catch",
                                    "param_types": [original_types],
                                    "code": unparse(copy_method2)
                                 })
                                 id += 1
                except Exception: pass
            
        return result
    def concurrentinject(target_method_node):
        # 植入bug 4： Concurrent Modification Failures 迭代期间修改集合，线程安全违规
        print("正在植入 Bug: Concurrent Modification Failures")
        result = []
        id = 0
        # 1) 对 MethodDeclaration 移除 synchronized,async,lock 修饰符（方法级别的线程安全失效）
        # for path, mnode in target_method_node.filter(MethodDeclaration):
        #     mods = getattr(mnode, "modifiers", None) or set()
        #     if "synchronized" in mods:
        #         copy_method = copy.deepcopy(target_method_node)
        #         removed = False
        #         for p2, m2 in copy_method.filter(MethodDeclaration):
        #             try:
        #                 m2_mods = getattr(m2, "modifiers", None) or set()
        #                 if "synchronized" in m2_mods:
        #                     # create new modifiers set without 'synchronized'
        #                     new_mods = set(m2_mods)
        #                     new_mods.discard("synchronized")
        #                     try:
        #                         m2.modifiers = new_mods
        #                     except Exception:
        #                         # some nodes may use list
        #                         try:
        #                             m2.modifiers = list(new_mods)
        #                         except Exception:
        #                             pass
        #                     result.append({
        #                         "bug_id": id,
        #                         "bug_type": "Concurrent Modification Failures",
        #                         "mutation": "remove_method_synchronized",
        #                         "method": getattr(m2, "name", None),
        #                         "code": unparse(copy_method)
        #                     })
        #                     id += 1
        #                     removed = True
        #             except Exception:
        #                 pass
        #             if removed:
        #                 break
        #         # go to next candidate
        #         if removed:
        #             continue
        #     elif "volatile" in mods:
        #         copy_method = copy.deepcopy(target_method_node)
        #         removed = False
        #         for p2, m2 in copy_method.filter(MethodDeclaration):
        #             try:
        #                 m2_mods = getattr(m2, "modifiers", None) or set()
        #                 if "volatile" in m2_mods:
        #                     # create new modifiers set without 'volatile'
        #                     new_mods = set(m2_mods)
        #                     new_mods.discard("volatile")
        #                     try:
        #                         m2.modifiers = new_mods
        #                     except Exception:
        #                         # some nodes may use list
        #                         try:
        #                             m2.modifiers = list(new_mods)
        #                         except Exception:
        #                             pass
        #                     result.append({
        #                         "bug_id": id,
        #                         "bug_type": "Concurrent Modification Failures",
        #                         "mutation": "remove_method_volatile",
        #                         "method": getattr(m2, "name", None),
        #                         "code": unparse(copy_method)
        #                     })
        #                     id += 1
        #                     removed = True
        #             except Exception:
        #                 pass
        #             if removed:
        #                 break
        #         # go to next candidate
        #         if removed:
        #             continue
 
        def _unwrap_synchronized_in_parent(sync_node, parent):
            """
            Replace the synchronized block statement in parent with the inner block statements.
            Handles several AST variants: parent.statements list, BlockStatement wrapper (.statement),
            parent.body / parent.block containers (including when those attrs are plain lists),
            and fallbacks using parent.attrs lists.
            """
            try:
                # accept block being a Block-like object or a plain list/tuple (javalang variants)
                block = getattr(sync_node, "block", None) or getattr(sync_node, "body", None)
                if block is None:
                    return False
                if isinstance(block, (list, tuple)):
                    stmts_to_insert = list(block)
                else:
                    stmts_to_insert = getattr(block, "statements", None)
                    if stmts_to_insert is None:
                        return False
                    stmts_to_insert = list(stmts_to_insert)

                def _replace_in_list(stmts):
                    for idx, s in enumerate(list(stmts)):
                        if s is sync_node:
                            stmts[idx:idx+1] = stmts_to_insert
                            return True
                        try:
                            if hasattr(s, "statement") and getattr(s, "statement") is sync_node:
                                stmts[idx:idx+1] = stmts_to_insert
                                return True
                        except Exception:
                            pass
                        try:
                            if hasattr(s, "expression") and getattr(s, "expression") is sync_node:
                                stmts[idx:idx+1] = stmts_to_insert
                                return True
                        except Exception:
                            pass
                    return False

                # Case A: parent.statements
                if hasattr(parent, "statements") and getattr(parent, "statements") is not None:
                    if _replace_in_list(parent.statements):
                        return True

                # Case B: parent.statement single-slot (BlockStatement wrapper)
                if hasattr(parent, "statement"):
                    ps = getattr(parent, "statement")
                    if ps is sync_node:
                        if len(stmts_to_insert) == 1:
                            parent.statement = stmts_to_insert[0]
                        else:
                            ns = types.SimpleNamespace()
                            ns.statements = stmts_to_insert
                            parent.statement = ns
                        return True
                    try:
                        if hasattr(ps, "statements") and ps.statements is not None:
                            if _replace_in_list(ps.statements):
                                return True
                    except Exception:
                        pass

                # Case C: parent.body / parent.block (handle when container is Block-like OR plain list)
                for attr in ("body", "block"):
                    if hasattr(parent, attr):
                        container = getattr(parent, attr)
                        if container is None:
                            continue
                        # if container is a plain list of statements, try to replace in it and write back
                        if isinstance(container, (list, tuple)):
                            lv = list(container)
                            if _replace_in_list(lv):
                                try:
                                    setattr(parent, attr, lv)
                                except Exception:
                                    pass
                                return True
                        else:
                            try:
                                if hasattr(container, "statements") and container.statements is not None:
                                    if _replace_in_list(container.statements):
                                        return True
                                # container itself equals sync_node
                                if container is sync_node:
                                    if len(stmts_to_insert) == 1:
                                        setattr(parent, attr, stmts_to_insert[0])
                                    else:
                                        ns = types.SimpleNamespace()
                                        ns.statements = stmts_to_insert
                                        setattr(parent, attr, ns)
                                    return True
                            except Exception:
                                pass

                # Case D: scan parent.attrs lists (last resort)
                attrs = getattr(parent, "attrs", []) or []
                for a in attrs:
                    try:
                        v = getattr(parent, a)
                    except Exception:
                        v = None
                    if v is None:
                        continue
                    if isinstance(v, (list, tuple)):
                        lv = list(v)
                        for idx, it in enumerate(list(lv)):
                            if it is sync_node:
                                lv[idx:idx+1] = stmts_to_insert
                                try:
                                    setattr(parent, a, lv)
                                except Exception:
                                    pass
                                return True
                            try:
                                if hasattr(it, "statement") and getattr(it, "statement") is sync_node:
                                    lv[idx:idx+1] = stmts_to_insert
                                    try:
                                        setattr(parent, a, lv)
                                    except Exception:
                                        pass
                                    return True
                            except Exception:
                                pass
                    else:
                        if v is sync_node:
                            try:
                                if len(stmts_to_insert) == 1:
                                    setattr(parent, a, stmts_to_insert[0])
                                else:
                                    ns = types.SimpleNamespace()
                                    ns.statements = stmts_to_insert
                                    setattr(parent, a, ns)
                                return True
                            except Exception:
                                pass
                return False
            except Exception:
                return False

        # 2) 展开 synchronized 块（移除块级锁，造成并发风险）
        syncs = list(target_method_node.filter(SynchronizedStatement))
        for idx, (path, sync) in enumerate(syncs):
            copy_method = copy.deepcopy(target_method_node)
            occ = 0
            applied = False
            for p2, s2 in copy_method.filter(SynchronizedStatement):
                if occ == idx:
                    # 找到对应拷贝中的该序号节点，尝试 unwrap
                    parent = None
                    for anc in reversed(p2[:-1]):
                        if hasattr(anc, "statements") or hasattr(anc, "statement"):
                            parent = anc
                            break
                    if parent is None:
                        parent = p2[-2] if len(p2) >= 2 else None
                    if parent is None:
                        break
                    try:
                        if _unwrap_synchronized_in_parent(s2, parent):
                            result.append({
                                "bug_id": id,
                                "bug_type": "Concurrent Modification Failures",
                                "mutation": "unwrap_synchronized_block",
                                "target_index": idx,
                                "code": unparse(copy_method)
                            })
                            id += 1
                            applied = True
                    except Exception:
                        pass
                    break
                occ += 1
            # continue 到下一个同步块（不在同一拷贝中同时移除其他块）
            if applied:
                continue

        # New: 对常用并发控制方法进行变异（start->run, remove wait/notify/lock）
        target_methods_remove = ["lock", "unlock", "tryLock", "wait", "notify", "notifyAll", "join", "await", "signal", "signalAll", "yield", "sleep", "countDown", "cyclicBarrier", "await"]
        target_methods_replace = {"start": "run"}

        all_mis_conc = list(target_method_node.filter(_jtree.MethodInvocation))
        for idx, (path, mi) in enumerate(all_mis_conc):
            member = getattr(mi, "member", None)
            
            # Mutation: start() -> run()
            if member in target_methods_replace:
                replacement = target_methods_replace[member]
                copy_method = copy.deepcopy(target_method_node)
                try:
                    # find corresponding node in copy
                    copy_mis = list(copy_method.filter(_jtree.MethodInvocation))
                    if idx < len(copy_mis):
                        path2, mi2 = copy_mis[idx]
                        if getattr(mi2, "member", None) == member:
                            mi2.member = replacement
                            result.append({
                                "bug_id": id,
                                "bug_type": "Concurrent Modification Failures",
                                "mutation": "replace_thread_call",
                                "original_call": member,
                                "new_call": replacement,
                                "code": unparse(copy_method)
                            })
                            id += 1
                except Exception: pass

            # Mutation: Remove concurrency control calls
            if member in target_methods_remove:
                copy_method = copy.deepcopy(target_method_node)
                try:
                    copy_mis = list(copy_method.filter(_jtree.MethodInvocation))
                    if idx < len(copy_mis):
                        path2, mi2 = copy_mis[idx]
                        if getattr(mi2, "member", None) == member:
                             # Remove statement logic
                             parent = None
                             for anc in reversed(path2[:-1]):
                                 if hasattr(anc, "statements") or hasattr(anc, "statement"):
                                     parent = anc
                                     break
                             if parent is None:
                                 parent = path2[-2] if len(path2) >= 2 else None
                             
                             if parent and _remove_node_from_parent(mi2, parent):
                                 result.append({
                                     "bug_id": id,
                                     "bug_type": "Concurrent Modification Failures",
                                     "mutation": "remove_concurrency_call",
                                     "removed_call": member,
                                     "code": unparse(copy_method)
                                 })
                                 id += 1
                except Exception: pass

        # 3) 在循环内部引入集合修改：对循环内的集合 add/remove 调用复制一次（在迭代中产生 ConcurrentModificationException 的机会）
        loop_type_names = ("ForStatement", "WhileStatement", "DoStatement", "EnhancedForControl", "Foreach")  # 多做兼容检查
        for path, mi in target_method_node.filter(_jtree.MethodInvocation):
            member = getattr(mi, "member", None)
            # 仅针对集合修改方法（add/remove）
            if member not in ("add", "remove", "clear", "put", "offer"):
                continue
            # 检查此调用是否位于某个循环体内（祖先路径中）
            in_loop = False
            for anc in path[:-1]:
                tname = type(anc).__name__
                if any(lt == tname or lt in tname for lt in loop_type_names):
                    in_loop = True
                    break
            if not in_loop:
                continue
            # 生成变体：在包含此方法调用的父 statements 中插入一次相同的调用（复制调用）
            try:
                call_s = unparse(mi)
            except Exception:
                call_s = None
            copy_method = copy.deepcopy(target_method_node)
            modified = False
            for p2, mi2 in copy_method.filter(_jtree.MethodInvocation):
                try:
                    if getattr(mi2, "member", None) == member and (call_s is None or unparse(mi2) == call_s):
                        # find enclosing parent that has statements list
                        parent = None
                        for anc in reversed(p2[:-1]):
                            if hasattr(anc, "statements") or hasattr(anc, "statement"):
                                parent = anc
                                break
                        if parent is None:
                            parent = p2[-2] if len(p2) >= 2 else None
                        if parent is None:
                            continue
                        # find statement index and insert a deepcopy of the MethodInvocation as a StatementExpression
                        if hasattr(parent, "statements") and getattr(parent, "statements") is not None:
                            stmts = parent.statements
                            for idx, s in enumerate(list(stmts)):
                                # match by rendered text or node identity
                                try:
                                    if s is mi2 or unparse(s).find(unparse(mi2)) != -1:
                                        # insert duplicate call right before original
                                        dup = copy.deepcopy(mi2)
                                        # wrap into StatementExpression node if necessary
                                        try:
                                            se = _jtree.StatementExpression(expression=dup)
                                        except Exception:
                                            # fallback: use StatementExpression alias
                                            se = StatementExpression(expression=dup)
                                        stmts.insert(idx, se)
                                        modified = True
                                        break
                                except Exception:
                                    continue
                        elif hasattr(parent, "statement") and getattr(parent, "statement") is mi2:
                            # parent.statement = sequence of two calls (use a Block-like namespace)
                            try:
                                dup = copy.deepcopy(mi2)
                                se1 = StatementExpression(expression=dup)
                                se2 = StatementExpression(expression=mi2)
                                ns = types.SimpleNamespace()
                                ns.statements = [se1, se2]
                                parent.statement = ns
                                modified = True
                            except Exception:
                                pass
                        if modified:
                            break
                except Exception:
                    continue
            if modified:
                result.append({
                    "bug_id": id,
                    "bug_type": "Concurrent Modification Failures",
                    "mutation": "duplicate_collection_mod_in_loop",
                    "member": member,
                    "sample_call": call_s,
                    "code": unparse(copy_method)
                })
                id += 1
        # 4) 在循环缺少终止条件导致无限循环
            # 1) for(...) -> make condition true (for (;;)/for (...; true; ...))
        def _collect_same_ord(node_cls, target_node):
            all_nodes = list(target_method_node.filter(node_cls))
            cond_s = None
            try:
                if hasattr(target_node, 'control'):
                    ctrl = getattr(target_node, 'control', None)
                    cond = getattr(ctrl, 'condition', None) if ctrl is not None else getattr(target_node, 'condition', None)
                else:
                    cond = getattr(target_node, 'condition', None)
                cond_s = unparse(cond) if cond is not None else None
            except Exception:
                cond_s = None
            same_ord = 0
            for _, n in all_nodes:
                try:
                    if hasattr(n, 'control'):
                        ctrln = getattr(n, 'control', None)
                        c = getattr(ctrln, 'condition', None) if ctrln is not None else getattr(n, 'condition', None)
                    else:
                        c = getattr(n, 'condition', None)
                    c_s = unparse(c) if c is not None else None
                except Exception:
                    c_s = None
                if cond_s is None:
                    if n is target_node:
                        break
                else:
                    if c_s == cond_s:
                        if n is target_node:
                            break
                        same_ord += 1
            return cond_s, same_ord

        # For -> make condition literal true (for (...; true; ...) )
        for path, for_node in target_method_node.filter(ForStatement):
            ctrl = getattr(for_node, "control", None)
            # Only support standard ForControl for infinite loop injection
            if not ctrl or type(ctrl).__name__ != 'ForControl':
                continue

            cond_s, same_ord = _collect_same_ord(ForStatement, for_node)
            copy_method = copy.deepcopy(target_method_node)
            occ = 0
            applied = False
            for p2, f2 in copy_method.filter(ForStatement):
                try:
                    ctrl2 = getattr(f2, "control", None)
                    cond2 = getattr(ctrl2, "condition", None) if ctrl2 is not None else getattr(f2, "condition", None)
                    cond2_s = unparse(cond2) if cond2 is not None else None
                except Exception:
                    cond2_s = None
                
                # Check consistency
                if cond_s is not None and cond2_s != cond_s:
                    continue
                # If both are None, we rely on ord. If one is None and other isn't, they are diff.
                if (cond_s is None) != (cond2_s is None):
                    continue

                if occ != same_ord:
                    occ += 1
                    continue
                try:
                    if ctrl2 is not None:
                        ctrl2.condition = Literal(value="true")
                        result.append({
                            "bug_id": id,
                            "bug_type": "Concurrent Modification Failures",
                            "mutation": "make_for_infinite",
                            "original_condition": cond_s,
                            "code": unparse(copy_method)
                        })
                        id += 1
                        applied = True
                except Exception:
                    pass
                if applied:
                    break

        # While -> while(true)
        for path, wnode in target_method_node.filter(WhileStatement):
            try:
                cond = getattr(wnode, "condition", None)
                cond_s = unparse(cond) if cond is not None else None
            except Exception:
                cond_s = None
            all_whiles = list(target_method_node.filter(WhileStatement))
            same_ord = 0
            for _, w in all_whiles:
                try:
                    cs = unparse(getattr(w, "condition", None))
                except Exception:
                    cs = None
                if cond_s is None:
                    if w is wnode:
                        break
                else:
                    if cs == cond_s:
                        if w is wnode:
                            break
                        same_ord += 1
            copy_method = copy.deepcopy(target_method_node)
            occ = 0
            applied = False
            for p2, w2 in copy_method.filter(WhileStatement):
                try:
                    if cond_s is not None and unparse(getattr(w2, "condition", None)) != cond_s:
                        continue
                except Exception:
                    pass
                if occ != same_ord:
                    occ += 1
                    continue
                try:
                    w2.condition = Literal(value="true")
                    result.append({
                        "bug_id": id,
                        "bug_type": "Concurrent Modification Failures",
                        "mutation": "make_while_infinite",
                        "original_condition": cond_s,
                        "code": unparse(copy_method)
                    })
                    id += 1
                    applied = True
                except Exception:
                    pass
                if applied:
                    break

        # Do-While -> while(true)
        for path, dnode in target_method_node.filter(_jtree.DoStatement):
            try:
                cond = getattr(dnode, "condition", None) or getattr(dnode, "expression", None)
                cond_s = unparse(cond) if cond is not None else None
            except Exception:
                cond_s = None
            all_dos = list(target_method_node.filter(_jtree.DoStatement))
            same_ord = 0
            for _, d in all_dos:
                try:
                    cs = unparse(getattr(d, "condition", None) or getattr(d, "expression", None))
                except Exception:
                    cs = None
                if cond_s is None:
                    if d is dnode:
                        break
                else:
                    if cs == cond_s:
                        if d is dnode:
                            break
                        same_ord += 1
            copy_method = copy.deepcopy(target_method_node)
            occ = 0
            applied = False
            for p2, d2 in copy_method.filter(_jtree.DoStatement):
                try:
                    cur_cond = getattr(d2, "condition", None) or getattr(d2, "expression", None)
                    if cond_s is not None and unparse(cur_cond) != cond_s:
                        continue
                except Exception:
                    pass
                if occ != same_ord:
                    occ += 1
                    continue
                try:
                    if hasattr(d2, "condition"):
                        d2.condition = Literal(value="true")
                    elif hasattr(d2, "expression"):
                        d2.expression = Literal(value="true")
                    result.append({
                        "bug_id": id,
                        "bug_type": "Concurrent Modification Failures",
                        "mutation": "make_do_while_infinite",
                        "original_condition": cond_s,
                        "code": unparse(copy_method)
                    })
                    id += 1
                    applied = True
                except Exception:
                    pass
                if applied:
                    break
        
        return result
    def incorrectinject(target_method_node):
        # 植入bug 5： Incorrect Behavior Failures（在“结果计算”中变异逻辑/比较/位运算符，导致返回/赋值错误）
        result = []
        id = 0
        print("正在植入 Bug: Incorrect Behavior Failures")

        def _is_in_result_computation(path, target_node):
            """
            判断 target_node 是否出现在“结果计算”语境中（例如：return expr, 赋值右侧, 变量初始化, 方法调用参数，表达式构成的计算结果等）。
            同时排除出现在条件判断/断言处的节点（若 ancestor 的 condition 与 target 匹配则返回 False）。
            path: tuple/list of ancestor nodes ending with target_node
            """
            try:
                ancestors = list(path[:-1])
            except Exception:
                ancestors = []

            # 如果出现在任何条件位（if/while/for/do/ternary/assert）的 condition 中，则不是结果计算
            for anc in reversed(ancestors):
                try:
                    cond = getattr(anc, "condition", None)
                    if cond is target_node:
                        return False
                    if cond is not None:
                        try:
                            if unparse(cond) == unparse(target_node):
                                return False
                        except Exception:
                            pass
                except Exception:
                    pass

            # 否则，如果目标被包裹在返回/赋值/变量初始化/方法调用参数/数组初始化等位置，视为结果计算
            for anc in reversed(ancestors):
                tname = type(anc).__name__
                try:
                    # return expr
                    if tname == "ReturnStatement":
                        expr = getattr(anc, "expression", None)
                        if expr is target_node:
                            return True
                        if expr is not None:
                            try:
                                if unparse(expr) == unparse(target_node):
                                    return True
                            except Exception:
                                pass
                    # assignment: value / expressionr / right
                    if tname in ("Assignment",):
                        val = getattr(anc, "value", None) or getattr(anc, "valuer", None) or getattr(anc, "expressionr", None) or getattr(anc, "right", None)
                        if val is target_node:
                            return True
                        if val is not None:
                            try:
                                if unparse(val) == unparse(target_node):
                                    return True
                            except Exception:
                                pass
                    # 处理 StatementExpression 包装 Assignment 的情况 ---
                    if tname == "StatementExpression":
                        expr = getattr(anc, "expression", None) or getattr(anc, "statement", None)
                        if expr is not None and type(expr).__name__ == "Assignment":
                            val = getattr(expr, "value", None) or getattr(expr, "expressionr", None) or getattr(expr, "right", None) or getattr(expr, "operandr", None)
                            if val is target_node:
                                return True
                            if val is not None:
                                try:
                                    if unparse(val) == unparse(target_node):
                                        return True
                                except Exception:
                                    pass
                    # variable declarator initializer
                    if tname in ("VariableDeclarator", "VariableDeclaratorId"):
                        init = getattr(anc, "initializer", None)
                        if init is target_node:
                            return True
                        if init is not None:
                            try:
                                if unparse(init) == unparse(target_node):
                                    return True
                            except Exception:
                                pass
                    # local var declaration -> check declarators' initializers
                    if tname in ("LocalVariableDeclaration", "VariableDeclaration"):
                        decs = getattr(anc, "declarators", None) or getattr(anc, "declarator", None)
                        if decs:
                            for d in (decs if isinstance(decs, (list, tuple)) else [decs]):
                                init = getattr(d, "initializer", None)
                                if init is target_node:
                                    return True
                                try:
                                    if init is not None and unparse(init) == unparse(target_node):
                                        return True
                                except Exception:
                                    pass
                    # method invocation arguments
                    if tname in ("MethodInvocation",):
                        args = getattr(anc, "arguments", None) or getattr(anc, "args", None)
                        if args:
                            for a in (args if isinstance(args, (list, tuple)) else [args]):
                                if a is target_node:
                                    return True
                                try:
                                    if a is not None and unparse(a) == unparse(target_node):
                                        return True
                                except Exception:
                                    pass
                    # array creation dims/initializer
                    if tname in ("ArrayCreation", "ArrayCreator", "ArrayInitializer"):
                        dims = getattr(anc, "dimensions", None) or getattr(anc, "initializer", None) or getattr(anc, "elements", None)
                        if dims:
                            try:
                                if isinstance(dims, (list, tuple)):
                                    for d in dims:
                                        if d is target_node:
                                            return True
                                        if d is not None and unparse(d) == unparse(target_node):
                                            return True
                                else:
                                    if dims is target_node or (dims is not None and unparse(dims) == unparse(target_node)):
                                        return True
                            except Exception:
                                pass
                    # binary operation that is itself nested inside a computation context (heuristic)
                    if tname == "BinaryOperation":
                        # if ancestor binary op is not used directly as a condition (checked above), consider this computation
                        return True
                except Exception:
                    continue
            return False

        # 对出现在“结果计算”中的二元运算进行变异（与 Logic Assertion Failures 类似，但用于返回/赋值/计算）
        op_map = {
            "+": "-",
            "-": "+",
            "*": "/",
            "/": "*",
            "%": "*",
            ">": "<",
            "<": ">",
            "!=": "==",
            '==': "!=",
            ">=": "<=",
            "<=": ">=",
            "&&": "||",
            "||": "&&",
            "&": "|",
            "|": "&",
            "^": "&",
            "<<": ">>",
            ">>": "<<",
            ">>>": ">>"
        }
        all_bins = list(target_method_node.filter(BinaryOperation))
        
        # New: Assignment operators
        assign_op_map = {
            "+=": "-=", "-=": "+=",
            "*=": "/=", "/=": "*=",
            "&=": "|=", "|=": "&=",
            "^=": "&=",
            "%=": "*=",
            "<<=": ">>=", ">>=": "<<=", ">>>=": ">>="
        }
        
        # New: Math.min <-> Math.max
        math_map = {"min": "max", "max": "min", "floor": "ceil", "ceil": "floor"}

        try:
            # Removed redundant "force_boolean_op_swap_in_return" loop as generic loop covers it.
            pass
        except Exception:
            pass
        
        for idx, (path, node) in enumerate(all_bins):
            origin = getattr(node, "operator", None)
            try:
                if not _is_in_result_computation(path, node):
                    continue
            except Exception:
                continue
            # 避免对字符串字面量做变异
            left_op = getattr(node, "operandl", None) or getattr(node, "left", None)
            right_op = getattr(node, "operandr", None) or getattr(node, "right", None)
            if _is_string_literal(left_op) or _is_string_literal(right_op):
                continue
            # 目标运算符集合：比较/逻辑/位运算
            if origin not in op_map:
                continue
            temp = op_map[origin]
            try:
                node_key = unparse(node)
            except Exception:
                node_key = None

            # 计算在所有二元操作中与当前节点文本相同的前置出现次数（ordinal）
            same_ord = 0
            for j in range(0, idx):
                try:
                    prev_key = unparse(all_bins[j][1])
                except Exception:
                    prev_key = None
                if node_key is None or prev_key == node_key:
                    # if node_key is None we still count by identity to keep ordinal stable;
                    # but unparse fallback above may cause prev_key==None, in that case rely on object identity
                    if node_key is None:
                        if all_bins[j][1] is not node:
                            same_ord += 1
                    else:
                        same_ord += 1 if prev_key == node_key else 0

            copy_method = copy.deepcopy(target_method_node)
            applied = False
            occ = 0
            for p2, b2 in copy_method.filter(BinaryOperation):
                try:
                    try:
                        b2_key = unparse(b2)
                    except Exception:
                        b2_key = None
                    # 只有当文本相同（或文本未知时通过序号匹配）时计数
                    match_text = (node_key is None and True) or (node_key is not None and b2_key == node_key)
                    if not match_text:
                        continue
                    if occ == same_ord:
                        # 定位到拷贝中对应的序号项，执行替换并产出变体
                        b2.operator = temp
                        result.append({
                            "bug_id": id,
                            "bug_type": "Incorrect Behavior Failures",
                            "mutation": "operator_mutation_in_result",
                            "origin_operator": origin,
                            "new_operator": temp,
                            "location": "result_computation",
                            "code": unparse(copy_method)
                        })
                        id += 1
                        applied = True
                        break
                    occ += 1
                except Exception:
                    continue
            # 若未在拷贝中按序号找到匹配项，则尝试回退到以前的文本匹配策略（兼容性）
            if not applied and node_key is not None:
                for p2, b2 in copy_method.filter(BinaryOperation):
                    try:
                        if unparse(b2) == node_key:
                            b2.operator = temp
                            result.append({
                                "bug_id": id,
                                "bug_type": "Incorrect Behavior Failures",
                                "mutation": "operator_mutation_in_result",
                                "origin_operator": origin,
                                "new_operator": temp,
                                "location": "result_computation",
                                "code": unparse(copy_method)
                            })
                            id += 1
                            break
                    except Exception:
                        continue

        # 对赋值操作符进行变异 (e.g. += -> -=)
        all_assigns = list(target_method_node.filter(_jtree.Assignment))
        for idx, (path, node) in enumerate(all_assigns):
            origin = getattr(node, "type", "=")
            if origin not in assign_op_map:
                continue
            
            temp = assign_op_map[origin]
            try:
                node_key = unparse(node)
            except Exception:
                node_key = None
            
            same_ord = 0
            for j in range(0, idx):
                try: prev_key = unparse(all_assigns[j][1])
                except Exception: prev_key = None
                if node_key is None or prev_key == node_key:
                    if node_key is None:
                        if all_assigns[j][1] is not node: same_ord += 1
                    else:
                        same_ord += 1 if prev_key == node_key else 0
            
            copy_method = copy.deepcopy(target_method_node)
            applied = False
            occ = 0
            for p2, b2 in copy_method.filter(_jtree.Assignment):
                try:
                    b2_key = unparse(b2)
                except Exception:
                    b2_key = None
                match_text = (node_key is None and True) or (node_key is not None and b2_key == node_key)
                if not match_text:
                    continue
                if occ == same_ord:
                    b2.type = temp
                    result.append({
                        "bug_id": id,
                        "bug_type": "Incorrect Behavior Failures",
                        "mutation": "assignment_operator_mutation",
                        "origin_operator": origin,
                        "new_operator": temp,
                        "location": "assignment",
                        "code": unparse(copy_method)
                    })
                    id += 1
                    applied = True
                    break
                occ += 1

        # 对常见的数学函数调用进行变异 (Math.min <-> Math.max 等)
        for idx, (path, mi) in enumerate(target_method_node.filter(_jtree.MethodInvocation)):
            qual = getattr(mi, "qualifier", "")
            member = getattr(mi, "member", "")
            
            if member not in math_map:
                continue
            
            if qual not in ("Math", "StrictMath", ""): 
                continue
            
            if qual == "" and member not in ("min", "max", "floor", "ceil", "round", "abs"):
                continue

            temp = math_map[member]
            try:
                node_key = unparse(mi)
            except Exception:
                node_key = None
                
            same_ord = 0
            all_mis = list(target_method_node.filter(_jtree.MethodInvocation))
            current_mi_idx = -1
            for k, (p_chk, m_chk) in enumerate(all_mis):
                if m_chk is mi:
                    current_mi_idx = k
                    break
            if current_mi_idx == -1: continue

            for j in range(0, current_mi_idx):
                try: prev_key = unparse(all_mis[j][1])
                except Exception: prev_key = None
                if node_key is None or prev_key == node_key:
                    if node_key is None:
                        if all_mis[j][1] is not mi: same_ord += 1
                    else:
                        same_ord += 1 if prev_key == node_key else 0

            copy_method = copy.deepcopy(target_method_node)
            applied = False
            occ = 0
            for p2, m2 in copy_method.filter(_jtree.MethodInvocation):
                 try:
                    m2_key = unparse(m2)
                 except: m2_key = None
                 match_text = (node_key is None and True) or (node_key is not None and m2_key == node_key)
                 if not match_text: continue
                 
                 if occ == same_ord:
                     m2.member = temp
                     result.append({
                        "bug_id": id,
                        "bug_type": "Incorrect Behavior Failures",
                        "mutation": "math_method_mutation",
                        "original_method": member,
                        "new_method": temp,
                        "location": "method_call",
                        "code": unparse(copy_method)
                     })
                     id += 1
                     applied = True
                     break
                 occ += 1

        # 变异一元操作符 (++, --)
        unary_swap = {"++": "--", "--": "++"}
        all_unary = []
        for path, node in target_method_node:
            if hasattr(node, "prefix_operators") and node.prefix_operators:
                if node.prefix_operators[-1] in unary_swap:
                    all_unary.append((path, node, "prefix"))
            if hasattr(node, "postfix_operators") and node.postfix_operators:
                if node.postfix_operators[-1] in unary_swap:
                    all_unary.append((path, node, "postfix"))

        for idx, (path, node, kind) in enumerate(all_unary):
            original_ops = node.prefix_operators if kind == "prefix" else node.postfix_operators
            op = original_ops[-1]
            new_op = unary_swap[op]
            
            try: node_key = unparse(node)
            except: node_key = None
            
            same_ord = 0
            for j in range(0, idx):
                p2, n2, k2 = all_unary[j]
                try: prev_key = unparse(n2)
                except: prev_key = None
                if node_key is None or prev_key == node_key:
                    if node_key is None:
                         if n2 is not node: same_ord += 1
                         elif k2 != kind: same_ord += 1
                    else:
                         same_ord += 1 if prev_key == node_key else 0
            
            copy_method = copy.deepcopy(target_method_node)
            applied = False
            occ = 0
            
            for p2, n2 in copy_method:
                events = []
                if hasattr(n2, "prefix_operators") and n2.prefix_operators and n2.prefix_operators[-1] in unary_swap:
                    events.append((n2, "prefix"))
                if hasattr(n2, "postfix_operators") and n2.postfix_operators and n2.postfix_operators[-1] in unary_swap:
                    events.append((n2, "postfix"))
                
                for (n_curr, k_curr) in events:
                    try: k_key = unparse(n_curr)
                    except: k_key = None
                    match_text = (node_key is None and True) or (node_key is not None and k_key == node_key)
                    if not match_text: continue
                    
                    if occ == same_ord:
                         target_list = n_curr.prefix_operators if k_curr == "prefix" else n_curr.postfix_operators
                         target_list[-1] = new_op
                         result.append({
                            "bug_id": id,
                            "bug_type": "Incorrect Behavior Failures",
                            "mutation": "unary_operator_mutation",
                            "origin_operator": op,
                            "new_operator": new_op,
                            "location": "unary_expression",
                            "code": unparse(copy_method)
                         })
                         id += 1
                         applied = True
                         break
                    occ += 1
                if applied: break

        # 额外变异：交换方法调用前两个参数（Method(a,b) -> Method(b,a)），作为 Incorrect Behavior Failures 的一类
        for path, mi in target_method_node.filter(_jtree.MethodInvocation):
            args = getattr(mi, "arguments", []) or []
            # 移除 _is_in_result_computation 检查，因为方法调用本身的参数顺序错误在任何语境（语句、条件、赋值）下都是有效的 Incorrect Behavior
            
            if len(args) < 2:
                continue
            # 尽量基于源码文本匹配，避免无意义的自交换
            try:
                a0_s = unparse(args[0])
                a1_s = unparse(args[1])
            except Exception:
                continue
            if not a0_s or not a1_s or a0_s == a1_s:
                continue
            qual = getattr(mi, "qualifier", None)
            member = getattr(mi, "member", None)

            copy_method = copy.deepcopy(target_method_node)
            applied = False
            for p2, mi2 in copy_method.filter(_jtree.MethodInvocation):
                try:
                    if getattr(mi2, "member", None) != member:
                        continue
                    # qualifier match when available to avoid cross-matching same-named methods
                    q2 = getattr(mi2, "qualifier", None)
                    if qual is not None:
                        try:
                            if unparse(q2) != unparse(qual):
                                continue
                        except Exception:
                            continue
                    mi2_args = getattr(mi2, "arguments", []) or []
                    if len(mi2_args) < 2:
                        continue
                    try:
                        if unparse(mi2_args[0]) == a0_s and unparse(mi2_args[1]) == a1_s:
                            # perform swap
                            mi2.arguments[0], mi2.arguments[1] = mi2.arguments[1], mi2.arguments[0]
                            result.append({
                                "bug_id": id,
                                "bug_type": "Incorrect Behavior Failures",
                                "mutation": "swap_method_args",
                                "member": member,
                                "original_args": [a0_s, a1_s],
                                "new_args": [unparse(mi2.arguments[0]), unparse(mi2.arguments[1])],
                                "location": "method_invocation_args",
                                "code": unparse(copy_method)
                            })
                            id += 1
                            applied = True
                    except Exception:
                        continue
                except Exception:
                    continue
                if applied:
                    break
        return result
    def logicinject(target_method_node):
        # 植入bug 6： Logic Assertion Failures 逻辑运算符错误 (&&/||)，比较运算符错误 (>/>=, </<=)，条件恒真或恒假，条件判断 / 断言
        result = []
        id = 0
        print("正在植入 Bug: Logic Assertion Failures")

        # 优化版 Context Check: 仅依赖 path 检查，移除昂贵的 fallback 全局搜索
        def _is_in_condition_context(path, target_node):
            if not path: return False
            
            # 快速检查: 遍历祖先链
            # javalang 的 path 包含从根到父节点的列表
            for i in range(len(path) - 1, -1, -1):
                anc = path[i]
                tname = type(anc).__name__
                
                # 能够包含 condition 的控制流语句
                if tname in ("IfStatement", "WhileStatement", "DoStatement", "DoWhileStatement", 
                             "ForStatement", "EnhancedForControl", "AssertStatement", 
                             "ConditionalExpression", "TernaryExpression"):
                    
                    # 检查是否是 condition / expression 属性
                    # (AssertStatement uses 'condition'; Ternary uses 'condition'; loops use 'condition')
                    cond = getattr(anc, "condition", None) or getattr(anc, "expression", None)
                    
                    # ForStatement 特殊处理: control 属性
                    if cond is None and hasattr(anc, "control"):
                        ctrl = getattr(anc, "control", None)
                        cond = getattr(ctrl, "condition", None) if ctrl is not None else None
                    
                    if cond is None: continue

                    # 判断 target_node 是否在 cond 子树中
                    # 由于我们是从 target 的 path 向上找，如果 cond 是 target 的祖先之一（或就是 cond 本身），
                    # 那么 target 一定在 cond 的子树里。
                    # 但要注意：control flow 里的 body 也是子树。必须区分 body 和 condition。
                    # 方法：检查 cond 是否在 path 中出现，或者 cond 就是 target_node
                    
                    if cond is target_node:
                        return True
                    
                    # 如果 cond 是复杂的表达式（如 (a > b) && (c < d)），target 可能是其中的孙节点
                    # 此时 cond 应该出现在 path 中（作为 target 的祖先）
                    # 但 javalang path 有时只包含 Block/Statement，不一定包含 Expression 级祖先。
                    # 所以最稳妥是：检查 cond 是否包含 target
                    
                    # 优化：不使用 unparse，使用对象引用检查
                    # 如果 target_node 在 cond 的子树下，那么 path 中从 anc 之后的某个节点应该等于 cond
                    # 或者，我们可以简单地做一次轻房子树遍历（比全方法遍历快得多）
                    
                    # 简单 heuristic: 
                    # 如果 ancestry 中的直接子节点属性名是 'condition' 或 'control'，则大概率在条件中
                    pass # 继续下面的通用检查
                
                # 通用属性检查：如果当前节点是父节点的 'condition' 属性，或者是 'control' 属性且父节点是循环
                parent = anc
                child = path[i+1] if i+1 < len(path) else target_node
                
                # 检查 child 是 parent 的哪个属性
                if hasattr(parent, "condition") and getattr(parent, "condition") is child:
                    return True
                if hasattr(parent, "expression") and getattr(parent, "expression") is child:
                    # expression 可能是 return expression (不是 condition)，也可能是 if (expr)
                    if tname in ("IfStatement", "WhileStatement", "DoStatement", "TernaryExpression", "ConditionalExpression", "AssertStatement"):
                        return True
                
                # For Loop logic
                if tname == "ForStatement" and hasattr(parent, "control") and getattr(parent, "control") is child:
                     # child is the ForControl
                     # Check if target is in ForControl.condition
                     if child is target_node: return False # target is the control node itself, wait for finer drill down
                     # If we are deeper, it means target is inside ForControl
                     # We need to check if it's inside ForControl.condition
                     pass 
                
                if tname in ("ForControl", "EnhancedForControl") and hasattr(parent, "condition") and getattr(parent, "condition") is child:
                    return True

            return False

        # 扩展二元运算符映射
        op_map = {
            ">": ["<", ">="],
            "<": [">", "<="],
            "!=": ["=="],
            "==": ["!="],
            ">=": ["<=", ">"],
            "<=": [">=", "<"],
            "&&": ["||"],
            "||": ["&&"],
            "&": ["|"],
            "|": ["&"],
            "^": ["&"],
        }
        
        # 1. BinaryOperation 变异 (使用 idx 直接映射，无需 unparse)
        all_bins = list(target_method_node.filter(BinaryOperation))
        for idx, (path, node) in enumerate(all_bins):
            # Context Check
            if not _is_in_condition_context(path, node):
                continue
            
            # 数据类型检查
            left = getattr(node, "operandl", None) or getattr(node, "left", None)
            right = getattr(node, "operandr", None) or getattr(node, "right", None)
            if _is_string_literal(left) or _is_string_literal(right):
                continue
                
            origin = getattr(node, "operator", None)
            if origin not in op_map:
                continue
            
            candidates = op_map[origin]
            
            # 对每一个候选变异符生成一个 mutant
            for new_op in candidates:
                copy_method = copy.deepcopy(target_method_node)
                # 直接通过索引定位
                current_bins = list(copy_method.filter(BinaryOperation))
                if idx < len(current_bins):
                    path2, b2 = current_bins[idx]
                    b2.operator = new_op
                    
                    result.append({
                        "bug_id": id,
                        "bug_type": "Logic Assertion Failures",
                        "mutation": "relational_operator_mutation",
                        "origin_operator": origin,
                        "new_operator": new_op,
                        "location": "condition_operator",
                        "code": unparse(copy_method)
                    })
                    id += 1

        # 2. 一元逻辑非变异 (remove negation: !cond -> cond)
        # 收集所有带 "!" 的节点及其 path
        unary_candidates = []
        # 使用通用 filter 遍历所有节点，检查 prefix_operators
        # list() to materialize traversal
        all_nodes_iter = list(target_method_node.filter(lambda n: True)) 
        
        # 筛选出带 ! 的 (idx, node, path)
        neg_indices = []
        c = 0
        for path, node in all_nodes_iter:
            # 必须是支持 prefix_operators 的节点
            if hasattr(node, "prefix_operators") and node.prefix_operators and "!" in node.prefix_operators:
                if _is_in_condition_context(path, node):
                    neg_indices.append(c)
            c += 1
            
        for target_idx in neg_indices:
            copy_method = copy.deepcopy(target_method_node)
            # 再次遍历找到对应索引的节点
            copy_iter = list(copy_method.filter(lambda n: True))
            if target_idx < len(copy_iter):
                path2, n2 = copy_iter[target_idx]
                if hasattr(n2, "prefix_operators") and "!" in n2.prefix_operators:
                    try:
                        n2.prefix_operators.remove("!")
                        result.append({
                            "bug_id": id,
                            "bug_type": "Logic Assertion Failures",
                            "mutation": "remove_negation",
                            "location": "condition_unary",
                            "code": unparse(copy_method)
                        })
                        id += 1
                    except: pass

        # 3. 增加逻辑非变异 (add negation: cond -> !cond)
        # 针对: 直接作为 If/While/Do 条件 或 &&/|| 操作数的 MemberReference/MethodInvocation
        add_neg_indices = []
        c = 0
        # 重新遍历一次全节点，因为这与上面的 filter 条件不同
        all_nodes_iter_2 = list(target_method_node.filter(lambda n: True))
        
        for path, node in all_nodes_iter_2:
            tname = type(node).__name__
            allowed_types = ("MemberReference", "MethodInvocation", "BinaryOperation", "ParenthesizedExpression")
            if tname in allowed_types:
                # 检查是否为 bool context
                is_bool = False
                if path:
                    parent = path[-1]
                    pname = type(parent).__name__
                    if pname in ("IfStatement", "WhileStatement", "DoStatement"):
                        cond = getattr(parent, "condition", None)
                        if cond is node: is_bool = True
                    elif pname == "BinaryOperation" and parent.operator in ("&&", "||"):
                        is_bool = True
                    elif pname == "ForControl" and getattr(parent, "condition", None) is node:
                        is_bool = True
                
                if is_bool:
                    # 避免双重否定
                    if not (hasattr(node, "prefix_operators") and node.prefix_operators and "!" in node.prefix_operators):
                        add_neg_indices.append(c)
            c += 1

        for target_idx in add_neg_indices:
            copy_method = copy.deepcopy(target_method_node)
            copy_iter = list(copy_method.filter(lambda n: True))
            if target_idx < len(copy_iter):
                path2, n2 = copy_iter[target_idx]
                if not hasattr(n2, "prefix_operators"):
                    setattr(n2, "prefix_operators", [])
                if n2.prefix_operators is None: n2.prefix_operators = []
                n2.prefix_operators.insert(0, "!")
                
                result.append({
                    "bug_id": id,
                    "bug_type": "Logic Assertion Failures",
                    "mutation": "add_negation",
                    "location": "condition_unary_add",
                    "code": unparse(copy_method)
                })
                id += 1

        # 4. 强制条件常量 (Condition Constant Replacement)
        # 仅针对 IfStatement 和 WhileStatement 的顶层条件
        ctrl_stmts = list(target_method_node.filter(IfStatement)) + \
                     list(target_method_node.filter(WhileStatement)) + \
                     list(target_method_node.filter(DoStatement))
        
        # 使用 (类型名, 索引) 来唯一定位，因为不同类型的 statement 列表是分开获取的
        # 为了统一定位，我们分别处理每种类型
        
        for st_type in [IfStatement, WhileStatement, DoStatement]:
            stmts_of_type = list(target_method_node.filter(st_type))
            for idx, (path, stmt) in enumerate(stmts_of_type):
                try:
                    cond = getattr(stmt, "condition", None)
                    if cond is None: continue
                    # 简单检查是否已经是字面量
                    if isinstance(cond, Literal) and str(cond.value) in ("true", "false"):
                        continue
                except: continue

                for bool_val in ("true", "false"):
                    copy_method = copy.deepcopy(target_method_node)
                    copy_stmts = list(copy_method.filter(st_type))
                    if idx < len(copy_stmts):
                        path2, s2 = copy_stmts[idx]
                        s2.condition = Literal(value=bool_val)
                        result.append({
                            "bug_id": id,
                            "bug_type": "Logic Assertion Failures",
                            "mutation": f"condition_to_{bool_val}",
                            "location": "control_flow_condition",
                            "code": unparse(copy_method)
                        })
                        id += 1

        return result                    
            # if node.operator == '==' or node.operator == '!=':
            #     origin = node.operator
            #     if node.operator == '==':
            #         temp = '!='
            #     else:
            #         temp = '=='
            #     node.operator = temp # <-- Bug 植入完成
            #     result.append({
            #         "bug_id" : id,
            #         "bug_type" : "Logic Assertion Failures",
            #         "code" : unparse(target_method_node)
            #     })
            #     id += 1
            #     node.operator = origin
    def datainject(target_method_node):
        # 植入bug 7： Data Integrity Failures 缺少负值检查，除零风险
        print("正在植入 Bug: Data Integrity Failures")
        result = []
        id = 0
        def _is_zero_literal(node):
            if node is None:
                return False
            try:
                v = getattr(node, "value", None)
                if v is None:
                    return False
                vs = str(v).strip()
                # accept integer/float literal forms like 0 0L 0.0
                return re.match(r"^-?0+(\.0+)?[lLfFdD]?$", vs) is not None
            except Exception:
                return False

        def _is_numeric_literal(node):
            if node is None:
                return False
            try:
                v = getattr(node, "value", None)
                if v is None:
                    return False
                vs = str(v).strip()
                return re.match(r"^-?\d+(\.\d+)?[lLfFdD]?$", vs) is not None
            except Exception:
                return False

        def _is_zero_guard(cond):
            """
            识别形如 x >= 0, x > 0, x < 0, x <= 0, x != 0, x == 0 等与 0 比较的 guard
            返回 (is_check, var_node, op, zero_side_string)
            """
            if cond is None or type(cond).__name__ != "BinaryOperation":
                return (False, None, None, None)
            op = getattr(cond, "operator", None)
            left = getattr(cond, "operandl", None)
            right = getattr(cond, "operandr", None)
            try:
                # support > >= < <= == !=
                valid_ops = (">", ">=", "<", "<=", "==", "!=")
                if _is_zero_literal(right) and (hasattr(left, "member") or hasattr(left, "name") or _is_numeric_literal(left) is False):
                    return (op in valid_ops, left, op, unparse(right))
                if _is_zero_literal(left) and (hasattr(right, "member") or hasattr(right, "name") or _is_numeric_literal(right) is False):
                    return (op in valid_ops, right, op, unparse(left))
            except Exception:
                pass
            return (False, None, None, None)

        # 1) 去掉针对零/负值的 guard -> 展开 then-block（移除负值检查/非负检查）
        # 改用 idx 定位，防止重复条件的多次变异总是命中第一个
        all_ifs = list(target_method_node.filter(IfStatement))
        for idx, (path, if_node) in enumerate(all_ifs):
            cond = getattr(if_node, "condition", None)
            is_chk, var_node, op, zero_s = _is_zero_guard(cond)
            if not is_chk:
                continue
            
            try: cond_s = unparse(cond)
            except: cond_s = "zero_check"

            copy_method = copy.deepcopy(target_method_node)
            try:
                copy_ifs = list(copy_method.filter(IfStatement))
                if idx < len(copy_ifs):
                    path2, if_node2 = copy_ifs[idx]
                    
                    parent = None
                    for anc in reversed(path2[:-1]):
                        if hasattr(anc, "statements") or hasattr(anc, "statement"):
                            parent = anc
                            break
                    if parent is None:
                        parent = path2[-2] if len(path2) >= 2 else None
                    
                    if parent and _unwrap_if_statement_in_parent(if_node2, parent):
                        result.append({
                            "bug_id": id,
                            "bug_type": "Data Integrity Failures",
                            "mutation": "remove_zero_negative_guard",
                            "removed_condition": cond_s,
                            "code": unparse(copy_method)
                        })
                        id += 1
            except Exception: pass

        # 2) 将除法/取模的除数替换为 0 -> 产生除零异常的变体
        # 收集所有可能的分母候选（文本形式或 None），随后为每个候选按出现序号生成变体
        denom_candidates = []
        for path, binop in target_method_node.filter(BinaryOperation):
            op = getattr(binop, "operator", None)
            if op not in ("/", "%"):
                continue
            denom = getattr(binop, "operandr", None) or getattr(binop, "right", None)
            # skip if denominator is string literal
            if _is_string_literal(denom):
                continue
            try:
                denom_s = unparse(denom)
            except Exception:
                denom_s = None
            denom_candidates.append((denom_s, op))

        # 为每个候选生成变体，支持同一文本出现多次的不同序号
        for idx_cand, (denom_s, op) in enumerate(denom_candidates):
            # 计算此候选在前面出现了多少次（用于按序号定位对应的节点）
            same_ord = 0
            for j in range(0, idx_cand):
                try:
                    if denom_candidates[j][0] == denom_s and denom_candidates[j][1] == op:
                        same_ord += 1
                except Exception:
                    continue

            copy_method = copy.deepcopy(target_method_node)
            modified = False
            occ = 0
            for p2, b2 in copy_method.filter(BinaryOperation):
                try:
                    if getattr(b2, "operator", None) != op:
                        continue
                    r2 = getattr(b2, "operandr", None) or getattr(b2, "right", None)
                    try:
                        r2_s = unparse(r2)
                    except Exception:
                        r2_s = None
                    # 匹配文本或使用序号（当文本为 None 时）
                    if (denom_s is None and occ == same_ord) or (denom_s is not None and r2_s == denom_s and occ == same_ord):
                        z = Literal(value="0")
                        if hasattr(b2, "operandr"):
                            b2.operandr = z
                        elif hasattr(b2, "right"):
                            b2.right = z
                        modified = True
                        break
                    # 如果当前 operator 匹配但文本不匹配，则仍计数，以保持序号一致性
                    if denom_s is None or r2_s == denom_s:
                        occ += 1
                except Exception:
                    continue
            if modified:
                result.append({
                    "bug_id": id,
                    "bug_type": "Data Integrity Failures",
                    "mutation": "denominator_to_zero",
                    "original_denominator": denom_s,
                    "code": unparse(copy_method)
                })
                id += 1

        # 3) 把数值字面量改为 0 或 负值（例如把 len -> 0 或 -len），可能导致边界/逻辑错误
        # for path, lit in target_method_node.filter(Literal):
        #     try:
        #         val = getattr(lit, "value", None)
        #         if val is None:
        #             continue
        #         vs = str(val).strip()
        #     except Exception:
        #         continue
        #     if re.match(r"^-?\d+(\.\d+)?[lLfFdD]?$", vs):
        #         # two mutations: to 0, and to negative version (if not already negative)
        #         for new_val, mut_name in (("0", "literal_to_zero"), (("-" + vs.lstrip("-")), "literal_to_negative")):
        #             # skip if already same
        #             if vs == new_val:
        #                 continue
        #             if vs == "0" or vs == "0.0":
        #                 continue
        #             copy_method = copy.deepcopy(target_method_node)
        #             changed = False
        #             for p2, lit2 in copy_method.filter(Literal):
        #                 try:
        #                     if getattr(lit2, "value", None) is None:
        #                         continue
        #                     if str(getattr(lit2, "value", None)).strip() == vs:
        #                         lit2.value = new_val
        #                         changed = True
        #                         # only change first matching literal instance per variant
        #                         break
        #                 except Exception:
        #                     continue
        #             if changed:
        #                 result.append({
        #                     "bug_id": id,
        #                     "bug_type": "Data Integrity Failures",
        #                     "mutation": mut_name,
        #                     "original_literal": vs,
        #                     "new_literal": new_val,
        #                     "code": unparse(copy_method)
        #                 })
        #                 id += 1

        # 4) 移除 isEmpty()/size()==0 等空/空集合检查 -> 展开 then-block（使空值/空集合未被校验）
        def _is_isEmpty_or_size_zero(node):
            # 检测直接的 isEmpty() 调用或 size()/length() == 0 比较
            if node is None:
                return (False, None)
            t = type(node).__name__
            try:
                if t == "MethodInvocation" and getattr(node, "member", None) == "isEmpty":
                    return (True, unparse(node))
                if t == "BinaryOperation":
                    op = getattr(node, "operator", None)
                    left = getattr(node, "operandl", None)
                    right = getattr(node, "operandr", None)
                    # patterns like coll.size() == 0  or coll.length == 0
                    if (_is_zero_literal(right) and type(left).__name__ in ("MethodInvocation", "MemberReference") and getattr(left, "member", None) in ("size", "length")):
                        return (True, unparse(node))
                    if (_is_zero_literal(left) and type(right).__name__ in ("MethodInvocation", "MemberReference") and getattr(right, "member", None) in ("size", "length")):
                        return (True, unparse(node))
            except Exception:
                pass
            return (False, None)

        empty_check_conditions = []
        # 改用 idx 定位
        all_ifs = list(target_method_node.filter(IfStatement))
        for idx, (path, if_node) in enumerate(all_ifs):
            cond = getattr(if_node, "condition", None)
            ok, cond_s = _is_isEmpty_or_size_zero(cond)
            if not ok: continue
            
            try: cond_s = unparse(cond)
            except: cond_s = "empty_check"

            copy_method = copy.deepcopy(target_method_node)
            try:
                copy_ifs = list(copy_method.filter(IfStatement))
                if idx < len(copy_ifs):
                    path2, if_node2 = copy_ifs[idx]
                    
                    parent = None
                    for anc in reversed(path2[:-1]):
                        if hasattr(anc, "statements") or hasattr(anc, "statement"):
                            parent = anc
                            break
                    if parent is None:
                        parent = path2[-2] if len(path2) >= 2 else None

                    if parent and _unwrap_if_statement_in_parent(if_node2, parent):
                        result.append({
                            "bug_id": id,
                            "bug_type": "Data Integrity Failures",
                            "mutation": "remove_isEmpty_or_size_guard",
                            "removed_condition": cond_s,
                            "code": unparse(copy_method)
                        })
                        id += 1
            except Exception: pass   
        return result
    def numericinject(target_method_node):
        # 植入bug 8： Numeric Computation Failures 整数溢出、浮点溢出/精度问题、极端数值替换（如 Integer.MAX_VALUE / MIN_VALUE / 1e308 / -1e308）
        print("正在植入 Bug: Numeric Computation Failures")
        result = []
        id = 0
        def _is_int_literal_str(s):
            if s is None:
                return False
            # 仅匹配整型/长整型字面量 (e.g. 123, 123L)，排除带 f/d 后缀或者是浮点结构的
            return re.match(r"^-?\d+[lL]?$", s.strip()) is not None

        def _is_float_literal_str(s):
            if s is None:
                return False
            s = s.strip()
            # 匹配: 1.23, 1.23f, 1e5, 123f, 123d
            return (re.match(r"^-?\d+\.\d+([eE][-+]?\d+)?[fFdD]?$", s) is not None or 
                    re.match(r"^-?\d+[eE][-+]?\d+[fFdD]?$", s) is not None or
                    re.match(r"^-?\d+[fFdD]$", s) is not None)

        # 1) 把整数字面量改为 Integer.MAX_VALUE / Integer.MIN_VALUE（可能导致溢出）
        # 改用 idx 定位
        # all_lits_1 = list(target_method_node.filter(Literal))
        # for idx, (path, lit) in enumerate(all_lits_1):
        #     try:
        #         v = getattr(lit, "value", None)
        #         if v is None:
        #             continue
        #         vs = str(v).strip()
        #     except Exception:
        #         continue
        #     if _is_int_literal_str(vs):
        #         for new_val, mut_name in (("Integer.MAX_VALUE", "literal_to_int_max"), ("Integer.MIN_VALUE", "literal_to_int_min")):
        #             if vs == new_val:
        #                 continue
        #             copy_method = copy.deepcopy(target_method_node)
        #             try:
        #                 copy_lits = list(copy_method.filter(Literal))
        #                 if idx < len(copy_lits):
        #                     path2, lit2 = copy_lits[idx]
        #                     lit2.value = new_val
                            
        #                     result.append({
        #                         "bug_id": id,
        #                         "bug_type": "Numeric Computation Failures",
        #                         "mutation": mut_name,
        #                         "original_literal": vs,
        #                         "new_literal": new_val,
        #                         "code": unparse(copy_method)
        #                     })
        #                     id += 1
        #             except Exception: pass

        # 2) 对整数算术运算（+ - *）的右操作数替换为 Integer.MAX_VALUE，诱发溢出
        # 改用 idx 定位
        # all_bins = list(target_method_node.filter(BinaryOperation))
        # for idx, (path, binop) in enumerate(all_bins):
        #     op = getattr(binop, "operator", None)
        #     if op not in ("+", "-", "*"):
        #         continue
        #     try:
        #         right_s = unparse(getattr(binop, "operandr", None) or getattr(binop, "right", None))
        #     except Exception:
        #         right_s = None
        #     copy_method = copy.deepcopy(target_method_node)
        #     try:
        #         copy_bins = list(copy_method.filter(BinaryOperation))
        #         if idx < len(copy_bins):
        #             path2, b2 = copy_bins[idx]
                    
        #             # replace right operand with Integer.MAX_VALUE
        #             z = Literal(value="Integer.MAX_VALUE")
        #             if hasattr(b2, "operandr"):
        #                 b2.operandr = z
        #             elif hasattr(b2, "right"):
        #                 b2.right = z
                    
        #             result.append({
        #                 "bug_id": id,
        #                 "bug_type": "Numeric Computation Failures",
        #                 "mutation": "arith_rhs_to_int_max",
        #                 "operator": op,
        #                 "original_rhs": right_s,
        #                 "code": unparse(copy_method)
        #             })
        #             id += 1
        #     except Exception: pass

        # 3) 浮点字面量替换为极大/极小 double（可能导致溢出/下溢或精度问题）
        # 改用 idx 定位
        all_lits = list(target_method_node.filter(Literal))
        for idx, (path, lit) in enumerate(all_lits):
            try:
                v = getattr(lit, "value", None)
                if v is None:
                    continue
                vs = str(v).strip()
            except Exception:
                continue
            if _is_float_literal_str(vs):
                for new_val, mut_name in (("1e308", "float_to_large"), ("-1e308", "float_to_negative_large"), ("1e-320", "float_to_subnormal")):
                    if vs == new_val:
                        continue
                    copy_method = copy.deepcopy(target_method_node)
                    try:
                        copy_lits = list(copy_method.filter(Literal))
                        if idx < len(copy_lits):
                            path2, lit2 = copy_lits[idx]
                            lit2.value = new_val
                            
                            result.append({
                                "bug_id": id,
                                "bug_type": "Numeric Computation Failures",
                                "mutation": mut_name,
                                "original_literal": vs,
                                "new_literal": new_val,
                                "code": unparse(copy_method)
                            })
                            id += 1
                    except Exception: pass

        # 4) 将整数除法改成取模或把被除数替换为 Integer.MAX_VALUE（尝试触发边界/溢出异常等）
        # 改用 idx 定位，并重新获取所有二元运算以确保安全
        all_bins_div = list(target_method_node.filter(BinaryOperation))
        for idx, (path, binop) in enumerate(all_bins_div):
            op = getattr(binop, "operator", None)
            if op not in ("/", "%"):
                continue
            try:
                left_s = unparse(getattr(binop, "operandl", None) or getattr(binop, "left", None))
            except Exception:
                left_s = None
            # skip if left or right operand is string literal
            lnode = getattr(binop, "operandl", None) or getattr(binop, "left", None)
            rnode = getattr(binop, "operandr", None) or getattr(binop, "right", None)
            if _is_string_literal(lnode) or _is_string_literal(rnode):
                continue
            
            # Variant A: Replace Numerator with Integer.MAX_VALUE
            copy_method = copy.deepcopy(target_method_node)
            try:
                copy_bins = list(copy_method.filter(BinaryOperation))
                if idx < len(copy_bins):
                    path2, b2 = copy_bins[idx]
                    
                    z = Literal(value="Integer.MAX_VALUE")
                    if hasattr(b2, "operandl"):
                        b2.operandl = z
                    elif hasattr(b2, "left"):
                        b2.left = z
                    
                    result.append({
                        "bug_id": id,
                        "bug_type": "Numeric Computation Failures",
                        "mutation": "numerator_to_int_max_for_div_mod",
                        "operator": op,
                        "original_lhs": left_s,
                        "code": unparse(copy_method)
                    })
                    id += 1
            except Exception: pass

            # Variant B: Swap Division / Modulo
            copy_method = copy.deepcopy(target_method_node)
            try:
                copy_bins = list(copy_method.filter(BinaryOperation))
                if idx < len(copy_bins):
                    path2, b2 = copy_bins[idx]
                    new_op = "%" if op == "/" else "/"
                    b2.operator = new_op
                    
                    result.append({
                        "bug_id": id,
                        "bug_type": "Numeric Computation Failures",
                        "mutation": "div_mod_swap",
                        "original_op": op,
                        "new_op": new_op,
                        "code": unparse(copy_method)
                    })
                    id += 1
            except Exception: pass
        return result
    def stringinject(target_method_node):
        # 植入bug 9： String Processing Failures 删除编码校验、equals->==、移除trim/toLowerCase等预处理、错分隔符,移除.split
        print("正在植入 Bug: String Processing Failures")
        result = []
        id = 0

        # 通用替换辅助函数: 在 AST 父节点中将 old_node 替换为 new_node
        def _do_replace(parent, old_node, new_node):
            if parent is None: return False
            # 1. 检查常见的单值属性
            for attr in ["expression", "condition", "value", "qualifier", "initializer", "statement", 
                         "try_statement", "finally_block", "then_statement", "else_statement", "body", "selector"]:
                try:
                    if hasattr(parent, attr) and getattr(parent, attr) is old_node:
                        setattr(parent, attr, new_node)
                        return True
                except Exception: pass
            
            # 2. 检查列表属性 (statements, body, block, arguments, parameters, types, declarators, case_statements)
            for attr in ["statements", "body", "block", "arguments", "parameters", "types", "declarators", "case_statements"]:
                try:
                    if hasattr(parent, attr):
                        seq = getattr(parent, attr)
                        if isinstance(seq, list):
                            for i, item in enumerate(seq):
                                if item is old_node:
                                    seq[i] = new_node
                                    return True
                except Exception: pass
            return False

        # 1. 基于 MethodInvocation 的变异 (所有遍历均基于 idx 索引定位)
        all_mis = list(target_method_node.filter(_jtree.MethodInvocation))
        for idx, (path, mi) in enumerate(all_mis):
            member = getattr(mi, "member", "")
            qual = getattr(mi, "qualifier", None)
            args = getattr(mi, "arguments", []) or []
            
            # 1.1 equals(...) -> == (Reference Equality)
            if member == "equals" and len(args) == 1 and qual is not None:
                copy_method = copy.deepcopy(target_method_node)
                current_mis = list(copy_method.filter(_jtree.MethodInvocation))
                if idx < len(current_mis):
                    path2, mi2 = current_mis[idx]
                    parent = path2[-1] if path2 else None
                    left = getattr(mi2, "qualifier", None)
                    right = (getattr(mi2, "arguments", []) or [None])[0]
                    if parent and left and right:
                        # 构造 BinaryOperation: left == right
                        new_op = BinaryOperation(operator="==", operandl=left, operandr=right)
                        if _do_replace(parent, mi2, new_op):
                            result.append({
                                "bug_id": id, "bug_type": "String Processing Failures",
                                "mutation": "equals_to_double_eq",
                                "code": unparse(copy_method)
                            })
                            id += 1

            # 1.2 equalsIgnoreCase(...) -> equals(...) (Case Sensitivity)
            if member == "equalsIgnoreCase" and len(args) == 1:
                copy_method = copy.deepcopy(target_method_node)
                current_mis = list(copy_method.filter(_jtree.MethodInvocation))
                if idx < len(current_mis):
                    path2, mi2 = current_mis[idx]
                    mi2.member = "equals"
                    result.append({
                        "bug_id": id, "bug_type": "String Processing Failures",
                        "mutation": "equalsIgnoreCase_to_equals",
                        "code": unparse(copy_method)
                    })
                    id += 1

            # 1.3 移除字符串预处理: trim/toLowerCase/toUpperCase/strip -> 直接使用 caller
            preprocs = ("trim", "toLowerCase", "toUpperCase", "strip", "stripLeading", "stripTrailing")
            if member in preprocs and qual is not None:
                # 只有当 qualifier 本身是一个独立表达式时才便于替换
                copy_method = copy.deepcopy(target_method_node)
                current_mis = list(copy_method.filter(_jtree.MethodInvocation))
                if idx < len(current_mis):
                    path2, mi2 = current_mis[idx]
                    parent = path2[-1] if path2 else None
                    q2 = getattr(mi2, "qualifier", None)
                    if parent and q2:
                        if _do_replace(parent, mi2, q2):
                            result.append({
                                "bug_id": id, "bug_type": "String Processing Failures",
                                "mutation": "remove_string_preprocess",
                                "removed": member,
                                "code": unparse(copy_method)
                            })
                            id += 1

            # 1.4 Split相关变异
            if member == "split" and len(args) >= 1:
                # 1.4a 错误的分隔符
                arg0 = args[0]
                if isinstance(arg0, _jtree.Literal) and isinstance(arg0.value, str):
                    val = arg0.value.strip('"').strip("'") 
                    mapping = {",": ";", ":": ",", "\\t": ",", " ": ","}
                    if val in mapping:
                        copy_method = copy.deepcopy(target_method_node)
                        current_mis = list(copy_method.filter(_jtree.MethodInvocation))
                        if idx < len(current_mis):
                            path2, mi2 = current_mis[idx]
                            new_sep = mapping[val]
                            mi2.arguments[0].value = f'"{new_sep}"'
                            result.append({
                                "bug_id": id, "bug_type": "String Processing Failures",
                                "mutation": "wrong_split_separator",
                                "code": unparse(copy_method)
                            })
                            id += 1
                
                # 1.4b 移除 Split (变为单元素数组)
                copy_method = copy.deepcopy(target_method_node)
                current_mis = list(copy_method.filter(_jtree.MethodInvocation))
                if idx < len(current_mis):
                    path2, mi2 = current_mis[idx]
                    parent = path2[-1] if path2 else None
                    q2 = getattr(mi2, "qualifier", None)
                    if parent and q2:
                        # 构造 new String[]{ str }
                        try:
                            lit_val = f"new String[]{{ {unparse(q2)} }}"
                            lit_node = Literal(value=lit_val)
                            if _do_replace(parent, mi2, lit_node):
                                result.append({
                                    "bug_id": id, "bug_type": "String Processing Failures",
                                    "mutation": "remove_split_call",
                                    "code": unparse(copy_method)
                                })
                                id += 1
                        except: pass

            # 1.5 indexOf <-> lastIndexOf
            if member == "indexOf":
                copy_method = copy.deepcopy(target_method_node)
                current_mis = list(copy_method.filter(_jtree.MethodInvocation))
                if idx < len(current_mis):
                    path2, mi2 = current_mis[idx]
                    mi2.member = "lastIndexOf"
                    result.append({
                        "bug_id": id, "bug_type": "String Processing Failures",
                        "mutation": "indexOf_to_lastIndexOf",
                        "code": unparse(copy_method)
                    })
                    id += 1
            elif member == "lastIndexOf":
                copy_method = copy.deepcopy(target_method_node)
                current_mis = list(copy_method.filter(_jtree.MethodInvocation))
                if idx < len(current_mis):
                    path2, mi2 = current_mis[idx]
                    mi2.member = "indexOf"
                    result.append({
                        "bug_id": id, "bug_type": "String Processing Failures",
                        "mutation": "lastIndexOf_to_indexOf",
                        "code": unparse(copy_method)
                    })
                    id += 1

            # 1.6 matches -> contains (Regex语义错误)
            if member == "matches":
                copy_method = copy.deepcopy(target_method_node)
                current_mis = list(copy_method.filter(_jtree.MethodInvocation))
                if idx < len(current_mis):
                    path2, mi2 = current_mis[idx]
                    mi2.member = "contains"
                    result.append({
                        "bug_id": id, "bug_type": "String Processing Failures",
                        "mutation": "matches_to_contains",
                        "code": unparse(copy_method)
                    })
                    id += 1

            # 1.7 Regex Bypass (matches("regex") -> matches(".*"))
            # 1.8 Regex Anchor Drop (matches("^regex$") -> matches("regex"))
            if member == "matches" and len(args) == 1 and isinstance(args[0], _jtree.Literal):
                val = getattr(args[0], "value", "")
                
                # Bypass
                if ("^" in val or "$" in val or "[" in val):
                    copy_method = copy.deepcopy(target_method_node)
                    current_mis = list(copy_method.filter(_jtree.MethodInvocation))
                    if idx < len(current_mis):
                        path2, mi2 = current_mis[idx]
                        mi2.arguments[0].value = '".*"'
                        result.append({
                            "bug_id": id, "bug_type": "String Processing Failures",
                            "mutation": "regex_bypass_matches",
                            "code": unparse(copy_method)
                        })
                        id += 1
                
                # Anchor Drop
                if ("^" in val or "$" in val):
                    copy_method = copy.deepcopy(target_method_node)
                    current_mis = list(copy_method.filter(_jtree.MethodInvocation))
                    if idx < len(current_mis):
                        path2, mi2 = current_mis[idx]
                        new_v = val.replace("^", "").replace("$", "")
                        mi2.arguments[0].value = new_v
                        result.append({
                            "bug_id": id, "bug_type": "String Processing Failures",
                            "mutation": "regex_remove_anchors",
                            "code": unparse(copy_method)
                        })
                        id += 1
            
            # 1.9 getBytes Encoding Mutation
            if member == "getBytes" and len(args) == 1 and isinstance(args[0], Literal):
                val = getattr(args[0], "value", "")
                if "UTF-8" in val.upper() or "UTF8" in val.upper():
                    copy_method = copy.deepcopy(target_method_node)
                    current_mis = list(copy_method.filter(_jtree.MethodInvocation))
                    if idx < len(current_mis):
                        path2, mi2 = current_mis[idx]
                        mi2.arguments[0].value = '"ISO-8859-1"'
                        result.append({
                            "bug_id": id, "bug_type": "String Processing Failures",
                            "mutation": "change_encoding_getBytes",
                            "code": unparse(copy_method)
                        })
                        id += 1

            # 1.10 Pattern.compile Bypass
            if member == "compile" and qual == "Pattern" and len(args) >= 1 and isinstance(args[0], Literal):
                # Pattern.compile(regex) -> Pattern.compile(".*")
                copy_method = copy.deepcopy(target_method_node)
                current_mis = list(copy_method.filter(_jtree.MethodInvocation))
                if idx < len(current_mis):
                    path2, mi2 = current_mis[idx]
                    mi2.arguments[0].value = '".*"'
                    result.append({
                        "bug_id": id, "bug_type": "String Processing Failures",
                        "mutation": "regex_bypass_pattern_compile",
                        "code": unparse(copy_method)
                    })
                    id += 1

        # 2. Constructor Mutations (new String(bytes, charset) -> new String(bytes))
        all_ccs = list(target_method_node.filter(_jtree.ClassCreator))
        for idx, (path, cc) in enumerate(all_ccs):
            typ = getattr(cc, "type", None)
            try: typ_s = unparse(typ) if typ else ""
            except: typ_s = ""
            if typ_s.endswith("String"):
                args = getattr(cc, "arguments", []) or []
                if len(args) == 2:
                    a2 = args[1]
                    if isinstance(a2, Literal) and "UTF" in getattr(a2, "value", "").upper():
                        copy_method = copy.deepcopy(target_method_node)
                        current_ccs = list(copy_method.filter(_jtree.ClassCreator))
                        if idx < len(current_ccs):
                            path2, cc2 = current_ccs[idx]
                            cc2.arguments = [cc2.arguments[0]]
                            result.append({
                                "bug_id": id, "bug_type": "String Processing Failures",
                                "mutation": "remove_newString_charset",
                                "code": unparse(copy_method)
                            })
                            id += 1
        
        return result
    def returninject(target_method_node):
        # 植入bug 10： Return Failures (Mutate return values)
        print("正在植入 Bug: Return Failures")
        result = []
        id = 0
        
        # 1. 获取并分析方法返回类型
        try:
            method_ret = getattr(target_method_node, "return_type", None)
            # unparse 可能会出错，或者返回 None (构造函数)
            ret_type_s = unparse(method_ret).strip() if method_ret is not None else ""
        except Exception:
            ret_type_s = ""

        # Void 方法或构造函数不处理返回值变异
        if ret_type_s in ("void", ""):
            return result

        # 2. 根据类型预生成候选值
        candidates = []
        
        # 基础数据类型
        if ret_type_s in ("int", "short", "byte", "long"):
            # 考虑 0, -1, 1, 极值
            candidates = ["0", "-1", "1", "Integer.MAX_VALUE", "Integer.MIN_VALUE"]
        elif ret_type_s == "char":
            candidates = ["'\\0'", "'a'"]
        elif ret_type_s == "boolean":
            candidates = ["true", "false"]
        elif ret_type_s in ("float", "double", "Float", "Double"):
            candidates = ["0.0", "-1.0", "1.0", "Double.NaN", "Double.POSITIVE_INFINITY"]
        else:
            # 引用类型：首先均可尝试 null
            candidates.append("null")
            
            # 简单的启发式类型匹配 (基于 unparse 的字符串)
            if "List" in ret_type_s or "ArrayList" in ret_type_s or "LinkedList" in ret_type_s:
                candidates.append("java.util.Collections.emptyList()")
            elif "Set" in ret_type_s or "HashSet" in ret_type_s:
                candidates.append("java.util.Collections.emptySet()")
            elif "Map" in ret_type_s or "HashMap" in ret_type_s:
                candidates.append("java.util.Collections.emptyMap()")
            elif "Optional" in ret_type_s:
                candidates.append("java.util.Optional.empty()")
            elif "String" in ret_type_s:
                candidates.append('""')
                candidates.append('"ERROR"')
            elif "[]" in ret_type_s:
                 # 数组类型：难以确定具体 Component Type，暂不自动生成 new T[0] 以免语法错误，
                 # 除非能精确解析。null 已经包含在上方。
                 pass

        if not candidates:
            return result

        # 3. 遍历 ReturnStatement (使用索引定位优化性能)
        all_returns = list(target_method_node.filter(ReturnStatement))
        
        for idx, (path, ret) in enumerate(all_returns):
            # 获取原返回值的字符串表示（用于判重）
            orig_expr = getattr(ret, "expression", None)
            try:
                orig_s = unparse(orig_expr).strip() if orig_expr is not None else "null"
            except Exception:
                orig_s = ""

            for cand in candidates:
                # 过滤等价变异: 如果候选代码与原代码相同，则无意义
                # (注意：简单的字符串比对可能错过部分情况，但已足够处理字面量)
                if cand == orig_s:
                    continue
                # 特殊布尔值检查 (避免 boolean 变量被替换为同值的字面量，虽少见但为了保险)
                if ret_type_s == "boolean":
                    if orig_s == "true" and cand == "true": continue
                    if orig_s == "false" and cand == "false": continue
                
                # 创建变体
                copy_method = copy.deepcopy(target_method_node)
                current_returns = list(copy_method.filter(ReturnStatement))
                
                if idx < len(current_returns):
                    path2, ret2 = current_returns[idx]
                    try:
                        # javalang 的 Literal 可以承载任意代码片段作为 value
                        ret2.expression = Literal(value=cand)
                        
                        result.append({
                            "bug_id": id,
                            "bug_type": "Return Failures",
                            "mutation": "mutate_return_value",
                            "original_return": orig_s,
                            "new_return": cand,
                            "code": unparse(copy_method)
                        })
                        id += 1
                    except Exception:
                        pass
        return result

    @staticmethod
    def buginject(java_code , extracted_method_name):
        try:
            tree = javalang.parse.parse(java_code)
            target_method_node = None
            for path, node in tree.filter(MethodDeclaration):
                if node.name == extracted_method_name:
                    target_method_node = node
                    break
        except javalang.parser.JavaSyntaxError as e:
            print(f"解析错误: {e}")
            target_method_node = None
            return None
        if not target_method_node:
            print("未找到目标方法节点或解析失败。")
            return None
        
        all_bugs = BugInject.nullinject(target_method_node) + \
                   BugInject.Indexinject(target_method_node) + \
                   BugInject.resouceinject(target_method_node) + \
                   BugInject.concurrentinject(target_method_node) + \
                   BugInject.incorrectinject(target_method_node) + \
                   BugInject.logicinject(target_method_node) + \
                   BugInject.numericinject(target_method_node) + \
                   BugInject.datainject(target_method_node) + \
                   BugInject.stringinject(target_method_node) + \
                   BugInject.returninject(target_method_node)
                   
        # 去重逻辑：基于 'code' 字段和 'bug_type' 去重
        unique_bugs = []
        seen_codes = set()
        
        for bug in all_bugs:
            # 使用 (code, bug_type) 元组作为唯一标识，也可以仅用 code
            # 这里为了保险，防止不同类型但代码碰巧相同的误判（虽然极少），采用了简单去重
            # 实际上仅仅去重 code 应该就足够了，因为如果 code 一样，行为就一样
            code_content = bug.get('code', '').strip()
            if code_content and code_content not in seen_codes:
                seen_codes.add(code_content)
                unique_bugs.append(bug)
                
        return unique_bugs