
import json
import os
from utils import *
with open('rl_train_end.json','r',encoding='utf-8') as f: # 0 -> 1w
    data = json.load(f)

sum = 0

for i in range(len(data)):
    data[i]['main_method_path'] = data[i]['main_method_path'].replace("fdse/rmy", "ubuntu/myren")
    data[i]['test_method_path'] = data[i]['test_method_path'].replace("fdse/rmy", "ubuntu/myren")
    if 'quantified_assertions' in data[i]:
        del data[i]['quantified_assertions']
with open(f"rl_train_end.json","w",encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(sum)

# import json
# import os
# # from utils import *  # 移除会导致报错的 utils 模块引用
# # 使用刚刚筛选出来的 11 个项目的数据集
# with open('sft_train.json','r',encoding='utf-8') as f: 
#     data = json.load(f)

# print(f"Loaded {len(data)} items from selected projects.")

# # 1. 定义统一的指令 (与 RL project_template 中的 code_nothink.jinja 保持一致)
# # 注意：SFT 阶段包含 Normal 和 Exception 断言，因此保留 Exception 相关的指令
# instruction_text = """
# # Role
# You are an expert in Java unit testing and JUnit 4 test oracle generation.

# # Task
# Given the focal method and a test prefix, infer the INTENDED behavior and produce ONE precise, fully functional JUnit 4 test oracle that validates that behavior.

# # Mandatory Rules
# 1. Assert ONLY on values returned or mutated by the focal method; ignore all setup variables.
# 2. NO SETUP CODE: do not create objects, assign variables, or call methods for preparation.
# 3. SINGLE OUTPUT: produce exactly one Java code block containing only the final oracle.
# 4. NO COMMENTS, NO EXPLANATIONS, NO PLACEHOLDERS, NO extra text.

# # Exception Handling Rule
# - If the test prefix ends with `// Undeclared exception!` output a try-catch-fail block in this exact pattern:
#     try { focalMethodCall(); fail("Expecting exception: SpecificException"); } catch(SpecificException e) {}
#     (replace SpecificException with the concrete exception class.)

# # Formatting & Style
# - Use JUnit 4 only and standard assertions (assertEquals, assertTrue, assertNotNull, assertSame, etc.).
# - Do NOT repeat or echo any content from the test prefix.
# - The output must be a single, compilable Java code block (one statement or one try-catch block).

# # Output
# Output ONLY the Java code block containing the oracle.
# """

# # 2. 定义输入模板 (保持与 code_nothink.jinja 的结构一致)
# input_template = """
# # Focal Method
# ```java
# {focal_method}
# ```

# # Test Prefix
# ```java
# {prefix}
# """

# sft_data = []

# for i in range(len(data)):
#     item = data[i]
    
#     # 构造输入
#     input_str = input_template.format(
#         focal_method=item.get('focal_method', ''),
#         prefix=item.get('prefix', '')
#     )

#     # 确定输出：SFT 必须包含 ```java 代码块包装
#     raw_output = item.get("assert", "")
#     output_str = f"```java\n{raw_output}\n```"

#     # 构造目标字典 (Llama-Factory 常用格式)
#     sft_item = {
#         "instruction": instruction_text,
#         "input": input_str,
#         "output": output_str
#     }
#     sft_data.append(sft_item)

# # split index must be an integer for slicing
# split = int(len(data))
# print(split)
# with open("sft_train_dataset.json", "w", encoding="utf-8") as f:
#     json.dump(sft_data, f, ensure_ascii=False, indent=2)

# with open("sft_test_dataset.json", "w", encoding="utf-8") as f:
#     json.dump(sft_data[split:], f, ensure_ascii=False, indent=2)
# print(f"Successfully generated SFT dataset with {len(sft_data)} items.")