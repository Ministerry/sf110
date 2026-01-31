from typing import List, Dict, Any, Optional
import javalang
from javalang.tree import MethodDeclaration, TryStatement, CatchClause, MethodInvocation
from openai import OpenAI, APIConnectionError
import json
import pandas as pd
import time
import os
import re
from count_long_methods import JavaMethodExtractor

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



if __name__ == "__main__":
    method = """   
public static void getModelos() throws Exception{
    Update.getModelo(\"http://jaw-br.sourceforge.net/modelos/Sistema%20Web%20I.modelo.xml\");
    Update.getModelo(\"http://jaw-br.sourceforge.net/modelos/Documentacao.modelo.xml\");
    }
    """

    prefix = """
public void test1()  throws Throwable  {
    Future<?> future = executor.submit(new Runnable(){ 
    public void run() {        
    try {         
    try {            
    Update.getModelos();            
    fail(\"Expecting exception: SecurityException\");         
    } catch(SecurityException e) {  
    /*           
    * Security manager blocks (java.net.SocketPermission jaw-br.sourceforge.net:80 connect,resolve)
    * java.lang.Thread.getStackTrace(Thread.java:1479)
    * org.evosuite.sandbox.MSecurityManager.checkPermission(MSecurityManager.java:303)
    * java.lang.SecurityManager.checkConnect(SecurityManager.java:1034)
    * sun.net.www.http.HttpClient.openServer(HttpClient.java:528)
    * sun.net.www.http.HttpClient.<init>(HttpClient.java:234)
    * sun.net.www.http.HttpClient.New(HttpClient.java:307)
    * sun.net.www.http.HttpClient.New(HttpClient.java:324)
    * sun.net.www.protocol.http.HttpURLConnection.getNewHttpClient(HttpURLConnection.java:970)
    * sun.net.www.protocol.http.HttpURLConnection.plainConnect(HttpURLConnection.java:911)
    * sun.net.www.protocol.http.HttpURLConnection.connect(HttpURLConnection.java:836)
    * sun.net.www.protocol.http.HttpURLConnection.getInputStream(HttpURLConnection.java:1172)
    * jaw.web.Update.getModelo(Update.java:32)
    * jaw.web.Update.getModelos(Update.java:19)
    * sun.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
    * sun.reflect.NativeMethodAccessorImpl.invoke(NativeMethodAccessorImpl.java:39)
    * sun.reflect.DelegatingMethodAccessorImpl.invoke(DelegatingMethodAccessorImpl.java:25)
    * java.lang.reflect.Method.invoke(Method.java:597)
    * org.evosuite.testcase.MethodStatement$1.execute(MethodStatement.java:262)
    * org.evosuite.testcase.AbstractStatement.exceptionHandler(AbstractStatement.java:142)
    * org.evosuite.testcase.MethodStatement.execute(MethodStatement.java:217)
    * org.evosuite.testcase.TestRunnable.call(TestRunnable.java:291)
    * org.evosuite.testcase.TestRunnable.call(TestRunnable.java:44)
    * java.util.concurrent.FutureTask$Sync.innerRun(FutureTask.java:303)
    * java.util.concurrent.FutureTask.run(FutureTask.java:138)
    * java.util.concurrent.ThreadPoolExecutor$Worker.runTask(ThreadPoolExecutor.java:886)
    * java.util.concurrent.ThreadPoolExecutor$Worker.run(ThreadPoolExecutor.java:908)
    * java.lang.Thread.run(Thread.java:662)
    */          
    }        
    } catch(Throwable t) {
        // Need to catch declared exceptions
        }
        } 
        }); 
        future.get(6000, TimeUnit.MILLISECONDS);   
    }
    """

    # with open("result_sf110.json","r",encoding="utf-8") as f:
    #     data = json.load(f)
    # prefix = data[10]['focal_prefix']
    # method = data[10]['focal_method']
    print(prefix)
    answer = extract_prefix_and_asserts(method, prefix)
    print(answer)
    print(len(answer))