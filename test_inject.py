# 修复后的测试脚本
import json
import re
from inject import BugInject
from inject import unparse
import javalang
import copy
import types
from javalang.tree import (
    MethodDeclaration, BlockStatement, ReturnStatement, BinaryOperation, Literal, Creator,
    FormalParameter,MethodInvocation, BasicType, IfStatement, LocalVariableDeclaration, ArraySelector,ForStatement,
    VariableDeclarator, StatementExpression, Assignment, ReferenceType, MemberReference,TryStatement,ArrayCreator,SynchronizedStatement
)
# if == null 展开 if != null 执行 ,但有时候你用里面的不一定会产生null bug
java_code = r'''
public class TestClass {
    public boolean containsSupportingDocument(SupportingDocument targetSupportingDocument) {
        int targetIdentifier = targetSupportingDocument.getIdentifier();
        for (SupportingDocument currentSupportingDocument : supportingDocuments) {
            int currentIdentifier
                = currentSupportingDocument.getIdentifier();
            if (targetIdentifier != currentIdentifier) {
                return true;
            }
        }

        return false;
    }
}
'''
target = javalang.parse.parse(java_code)
print(unparse(target))
print(target)
result = BugInject.buginject(java_code,"containsSupportingDocument")

print(f"生成变体数量: {len(result)}\n")
for i, item in enumerate(result):
    print(f"=== 变体 {i+1} ===")
    print("bug_id:", item.get("bug_id"))
    print("mutation:", item.get("mutation"))
    print("bug_type:", item.get("bug_type"))
    code = item.get("code") or item.get("patched") or item.get("variant_code")
    if code:
        print("------ 变异后代码（前1000字符） ------")
        print(code[:1000])
    else:
        print("无代码字段，内容：", json.dumps(item))
    print()