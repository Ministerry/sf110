import javalang
import sys
import os

def create_cfg(method_node):
    nodes = []
    edges = []
    
    # Helper to generate IDs
    nonlocal_counter = {'id': 0}
    
    def get_new_id():
        nid = f"node_{nonlocal_counter['id']}"
        nonlocal_counter['id'] += 1
        return nid

    def add_node(label, stmt=None, line=None):
        nid = get_new_id()
        if stmt and not line:
            line = getattr(stmt, 'position', None)
        nodes.append({"id": nid, "label": label, "stmt": stmt, "line": line})
        return nid

    def add_edge(u, v, label=""):
        if u and v:
            edges.append((u, v, label))

    # context = { 'break_target': id, 'continue_target': id, 'finally_target': id, 'labels': {} }
    def build_flow(stmts, parent_id, edge_label="", context=None):
        if context is None: context = {'labels': {}}
        last_node_id = parent_id
        current_edge_label = edge_label
        
        if not isinstance(stmts, list):
            stmts = [stmts] if stmts else []
            
        i = 0
        while i < len(stmts):
            stmt = stmts[i]
            i += 1
            
            # --- Block Unwrapping ---
            if isinstance(stmt, javalang.tree.BlockStatement):
                # Recurse immediately
                last_node_id = build_flow(stmt.statements, last_node_id, current_edge_label, context)
                current_edge_label = ""
                continue
                
            # --- Flow Control Statements causing Jumps (Break, Continue, Return, Throw) ---
            if isinstance(stmt, javalang.tree.BreakStatement):
                 # Create node
                label_txt = f"Break {stmt.goto}" if getattr(stmt, 'goto', None) else "Break"
                this_id = add_node(label_txt, stmt)
                if last_node_id: add_edge(last_node_id, this_id, current_edge_label)
                
                # Check for finally
                finally_target = context.get('finally_target')
                
                # Check for labeled break
                target_node = None
                jump_type = "jump"
                
                if getattr(stmt, 'goto', None):
                    lbl = stmt.goto
                    if 'labels' in context and lbl in context['labels']:
                        target_node = context['labels'][lbl].get('break')
                        jump_type = f"jump_label_{lbl}"
                else:
                    target_node = context.get('break_target')
                
                if finally_target:
                     # If there is a finally block, we go there first.
                     # But we need to know where to go AFTER finally. 
                     # This complexity is hard in static CFG without edge attributes.
                     # Simplified: edge to finally, knowing flow eventually reaches target.
                    add_edge(this_id, finally_target, "auto_finally")
                elif target_node:
                    add_edge(this_id, target_node, jump_type)
                
                # Control flow stops here for this path
                last_node_id = None 
                break 
                
            if isinstance(stmt, javalang.tree.ContinueStatement):
                label_txt = f"Continue {stmt.goto}" if getattr(stmt, 'goto', None) else "Continue"
                this_id = add_node(label_txt, stmt)
                if last_node_id: add_edge(last_node_id, this_id, current_edge_label)
                
                finally_target = context.get('finally_target')
                
                target_node = None
                jump_type = "jump"
                
                if getattr(stmt, 'goto', None):
                    lbl = stmt.goto
                    if 'labels' in context and lbl in context['labels']:
                        target_node = context['labels'][lbl].get('continue')
                        jump_type = f"jump_label_{lbl}"
                else:
                    target_node = context.get('continue_target')
                
                if finally_target:
                    add_edge(this_id, finally_target, "auto_finally")
                elif target_node:
                    add_edge(this_id, target_node, jump_type)
                
                last_node_id = None
                break

            if isinstance(stmt, javalang.tree.ReturnStatement):
                this_id = add_node("Return", stmt)
                if last_node_id: add_edge(last_node_id, this_id, current_edge_label)
                
                finally_target = context.get('finally_target')
                if finally_target:
                    add_edge(this_id, finally_target, "auto_finally")
                
                # Typically connects to End node of graph, but we might not have it referenceable here.
                last_node_id = None
                break
                
            if isinstance(stmt, javalang.tree.ThrowStatement):
                this_id = add_node("Throw", stmt)
                if last_node_id: add_edge(last_node_id, this_id, current_edge_label)
                
                finally_target = context.get('finally_target')
                if finally_target:
                    add_edge(this_id, finally_target, "auto_finally")
                
                # Sink node or jump to exception handler
                last_node_id = None
                break

            # --- Structural Statements ---
            
            if isinstance(stmt, javalang.tree.IfStatement):
                # Condition Node
                cond_str = str(stmt.condition) if stmt.condition else ""
                cond_id = add_node(f"If: {cond_str}", stmt)
                if last_node_id: add_edge(last_node_id, cond_id, current_edge_label)
                
                # Merge Node
                merge_id = add_node("Msg_If", None)
                
                # Then Branch
                then_end = build_flow(stmt.then_statement, cond_id, "True", context)
                if then_end: add_edge(then_end, merge_id, "")
                     
                # Else Branch
                if stmt.else_statement:
                    else_end = build_flow(stmt.else_statement, cond_id, "False", context)
                    if else_end: add_edge(else_end, merge_id, "")
                else:
                    # Direct link if no else
                    add_edge(cond_id, merge_id, "False")
                    
                last_node_id = merge_id
                current_edge_label = ""
                
            elif isinstance(stmt, (javalang.tree.WhileStatement, javalang.tree.ForStatement)):
                # Loop Header
                label_txt = "Loop"
                if hasattr(stmt, 'condition') and stmt.condition:
                    label_txt = f"Loop: {stmt.condition}"
                elif hasattr(stmt, 'control') and stmt.control:
                    label_txt = f"Loop: {stmt.control}"
                    
                header_id = add_node(label_txt, stmt)
                
                if last_node_id: add_edge(last_node_id, header_id, current_edge_label)
                
                # Merge Node (Target for breaks)
                after_loop_id = add_node("AfterLoop_Merge", None)
                
                loop_context = context.copy()
                loop_context['break_target'] = after_loop_id
                loop_context['continue_target'] = header_id 
                
                # Handle Loop Labels
                if getattr(stmt, 'label', None):
                    lbl = stmt.label
                    if 'labels' not in loop_context: loop_context['labels'] = {}
                    # Copy dict so we don't pollute parent context
                    loop_context['labels'] = loop_context['labels'].copy()
                    loop_context['labels'][lbl] = {'break': after_loop_id, 'continue': header_id}
                
                # Body
                body_end = build_flow(stmt.body, header_id, "True", loop_context)
                if body_end:
                    add_edge(body_end, header_id, "Back")
                    
                # Exit edge
                add_edge(header_id, after_loop_id, "False")
                
                last_node_id = after_loop_id
                current_edge_label = ""

            elif isinstance(stmt, javalang.tree.DoStatement):
                # Do entry
                entry_id = add_node("Do_Entry", None)
                if last_node_id: add_edge(last_node_id, entry_id, current_edge_label)
                
                # Break target
                after_loop_id = add_node("AfterDo_Merge", None)
                
                # Condition Node
                cond_id = add_node(f"DoCond: {stmt.condition}", stmt)
                
                loop_context = context.copy()
                loop_context['break_target'] = after_loop_id
                loop_context['continue_target'] = cond_id 
                
                if getattr(stmt, 'label', None):
                    lbl = stmt.label
                    if 'labels' not in loop_context: loop_context['labels'] = {}
                    loop_context['labels'] = loop_context['labels'].copy()
                    loop_context['labels'][lbl] = {'break': after_loop_id, 'continue': cond_id}
                
                # Body
                body_end = build_flow(stmt.body, entry_id, "", loop_context)
                
                if body_end:
                    add_edge(body_end, cond_id, "")
                else:
                    add_edge(entry_id, cond_id, "")

                # edges
                add_edge(cond_id, entry_id, "True")
                add_edge(cond_id, after_loop_id, "False")
                
                last_node_id = after_loop_id
                current_edge_label = ""
                
            elif isinstance(stmt, javalang.tree.SwitchStatement):
                # Selector
                sel_id = add_node(f"Switch: {stmt.selector}", stmt)
                if last_node_id: add_edge(last_node_id, sel_id, current_edge_label)
                
                # End node
                end_switch_id = add_node("Switch_Merge", None)
                
                switch_ctx = context.copy()
                switch_ctx['break_target'] = end_switch_id
                
                if getattr(stmt, 'label', None):
                    lbl = stmt.label
                    if 'labels' not in switch_ctx: switch_ctx['labels'] = {}
                    switch_ctx['labels'] = switch_ctx['labels'].copy()
                    switch_ctx['labels'][lbl] = {'break': end_switch_id, 'continue': None} # Switch can't continue unless in loop
                
                if stmt.cases:
                    prev_case_end = None
                    # Connect selector to each case start (simplified visual flow)
                    
                    for i, case_node in enumerate(stmt.cases):
                        # Case Label Node
                        label_txt = f"Case {case_node.case}" if case_node.case else "Default"
                        case_entry_id = add_node(label_txt, case_node)
                        
                        # Switch connects to this case selector
                        add_edge(sel_id, case_entry_id, "check")
                        
                        # Previous case falls through to here
                        if prev_case_end:
                            add_edge(prev_case_end, case_entry_id, "fallthrough")
                            
                        # Body
                        curr_end = build_flow(case_node.statements, case_entry_id, "match", switch_ctx)
                        prev_case_end = curr_end
                        
                    # Last case
                    if prev_case_end:
                        add_edge(prev_case_end, end_switch_id, "")
                else:
                    add_edge(sel_id, end_switch_id, "empty")
                    
                last_node_id = end_switch_id
                current_edge_label = ""
                
            elif isinstance(stmt, javalang.tree.SynchronizedStatement):
                lock_id = add_node(f"Sync: {stmt.lock}", stmt)
                if last_node_id: add_edge(last_node_id, lock_id, current_edge_label)
                
                last_node_id = build_flow(stmt.block, lock_id, "", context)
                current_edge_label = ""
                
            elif isinstance(stmt, javalang.tree.TryStatement):
                # Try Header
                try_id = add_node("Try", stmt)
                if last_node_id: add_edge(last_node_id, try_id, current_edge_label)
                
                final_merge_id = add_node("Try_Merge", None)
                
                if stmt.finally_block:
                    finally_id = add_node("Finally", None)
                    
                    # Create a new context for try/catch blocks that points to this finally
                    try_ctx = context.copy()
                    try_ctx['finally_target'] = finally_id
                    
                    # Try Body
                    try_end = build_flow(stmt.block, try_id, "", try_ctx)
                    
                    catch_nodes = []
                    if stmt.catches:
                        for catch_clause in stmt.catches:
                            c_id = add_node(f"Catch: {catch_clause.parameter.name}", catch_clause)
                            add_edge(try_id, c_id, "exception")
                            
                            c_end = build_flow(catch_clause.block, c_id, "", try_ctx)
                            catch_nodes.append(c_end)
                    
                    # Collect normal flow ends
                    ends_to_connect = []
                    if try_end: ends_to_connect.append(try_end)
                    ends_to_connect.extend([c for c in catch_nodes if c])
                    
                    # Connect normal flow to finally
                    for e in ends_to_connect:
                        add_edge(e, finally_id, "")
                    
                    # Build finally block (using original context, not the one with finally_target pointing to itself)
                    final_end = build_flow(stmt.finally_block, finally_id, "", context)
                    last_node_id = final_end
                    
                else:
                    # No finally, simple merge
                    try_end = build_flow(stmt.block, try_id, "", context)
                    
                    catch_nodes = []
                    if stmt.catches:
                        for catch_clause in stmt.catches:
                            c_id = add_node(f"Catch: {catch_clause.parameter.name}", catch_clause)
                            add_edge(try_id, c_id, "exception")
                            c_end = build_flow(catch_clause.block, c_id, "", context)
                            catch_nodes.append(c_end)
                            
                    ends_to_connect = []
                    if try_end: ends_to_connect.append(try_end)
                    ends_to_connect.extend([c for c in catch_nodes if c])
                    
                    for e in ends_to_connect:
                        add_edge(e, final_merge_id, "")
                        
                    last_node_id = final_merge_id
                    
                current_edge_label = ""

            else:
                # Regular Statement
                label = type(stmt).__name__
                if hasattr(stmt, 'expression'): label = f"Expr: {stmt.expression}"
                
                this_id = add_node(label, stmt)
                if last_node_id: add_edge(last_node_id, this_id, current_edge_label)
                
                last_node_id = this_id
                current_edge_label = ""
                
        return last_node_id

    # Entry point
    start_id = add_node("Start", None)
    
    if method_node.body:
        final_id = build_flow(method_node.body, start_id)
        
        end_id = add_node("End", None)
        if final_id:
            add_edge(final_id, end_id, "")
    else:
        end_id = add_node("End", None)
        add_edge(start_id, end_id, "")
        
    return nodes, edges

def generate_dot(nodes, edges):
    dot = "digraph CFG {\n"
    for node in nodes:
        nid = node['id']
        label = str(node['label']).replace('"', '\"')[:50]
        dot += f'    {nid} [label="{label}"];\n'
    for u, v, label in edges:
        dot += f'    {u} -> {v} [label="{label}"];\n'
    dot += "}\n"
    return dot

def create_dot_graph(nodes, edges, output_path):
    content = generate_dot(nodes, edges)
    with open(output_path, 'w') as f:
        f.write(content)
    print(f"Graph saved to {output_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python generate_cfg.py <java_file> <method_name>")
        sys.exit(1)
        
    file_path = sys.argv[1]
    method_name = sys.argv[2]
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    try:
        tree = javalang.parse.parse(content)
        target_method = None
        for path, node in tree.filter(javalang.tree.MethodDeclaration):
            if node.name == method_name:
                target_method = node
                break
        
        if not target_method:
            print(f"Method {method_name} not found")
            sys.exit(1)
            
        nodes, edges = create_cfg(target_method)
        print(generate_dot(nodes, edges))
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
