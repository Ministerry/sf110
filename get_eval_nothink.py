import os
import json
import pandas as pd
import sys

filename = sys.argv[1]
# 1. 自动定位输入文件
input_file = f"{filename}"
if not os.path.exists(input_file):
    # Try alternate location
    if os.path.exists(f"SF110/{filename}.json"):
        input_file = f"SF110/{filename}.json"
    else:
        # Fallback to absolute path or assume relative to script execution
        input_file = f"/home/ubuntu/myren/SF110/{filename}.json"

if not os.path.exists(input_file):
    print(f"Error: {input_file} not found")
    exit(1)

#打开json文件
with open(input_file, "r", encoding='utf-8') as file:
    data = json.load(file)

#确定数据集数量和格式
train_data = []
test_data = []
print("data的长度为：", len(data))

# 2. 统一 Input Template (去掉多余的换行和格式，与 self_rl_data.py 对齐)
input_template = """
# Focal Method
```java
{focal_method}
```

# Test Prefix
```java
{prefix}
```
"""

# 3. 统一 System Prompt (与 self_rl_data.py 完全一致)
# 注意：原 split_dataset.py 中的 prompt 包含了很多示例（few-shot），
# 但 self_rl_data.py 中只使用了 zero-shot 的指令。
# 为了评估的一致性，通常应保持一致。如果 self_rl_data.py 用于 RL 是 zero-shot，那评估也应该是 zero-shot。
# 这里我们用 self_rl_data.py 的版本。
# 
prompt = '''
# Role
You are an expert in Java unit testing and JUnit 4 test oracle generation.

# Task
Given the focal method and a test prefix, infer the INTENDED behavior and produce ONE precise, fully functional JUnit 4 test oracle that validates that behavior.

# Mandatory Rules
1. Assert ONLY on values returned or mutated by the focal method; ignore all setup variables.
2. NO SETUP CODE: do not create objects, assign variables, or call methods for preparation.
3. SINGLE OUTPUT: produce exactly one Java code block containing only the final oracle.
4. NO COMMENTS, NO EXPLANATIONS, NO PLACEHOLDERS, NO extra text.

# Exception Handling Rule
- If the test prefix ends with `// Undeclared exception!` output a try-catch-fail block in this exact pattern:
    try { focalMethodCall(); fail("Expecting exception: SpecificException"); } catch(SpecificException e) {}
    (replace SpecificException with the concrete exception class.)

# Formatting & Style
- Use JUnit 4 only and standard assertions (assertEquals, assertTrue, assertNotNull, assertSame, etc.).
- Do NOT repeat or echo any content from the test prefix.
- The output must be a single, compilable Java code block (one statement or one try-catch block).

# Output
Output ONLY the Java code block containing the oracle.
'''


# 4. 去除 input() 阻塞，改为参数化或默认值
# 默认 5000 或者按比例划分
DEFAULT_TRAIN_SIZE = 5000
train_length = DEFAULT_TRAIN_SIZE
# 或者 train_length = int(len(data) * 0.9)

print(f"Using train_length = {train_length}")

# ChatML 格式化函数
def format_chatml(system, user):
    return f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n"

for i in range(len(data)):
    pre = data[i]['prefix']
    assertion = data[i]['assert']
    code = data[i]['focal_method']
    
    # 保留原逻辑：只包含有变异体的数据
    if len(data[i].get('ast_generates', [])) != 0:
        input_text = input_template.format(focal_method=code, prefix=pre)
        user_content = input_text if input_text else "Please generate the assertion."
        
        # 构造 item
        # 为了与 RL 训练 (verl + code.jinja) 保持完全一致的输入分布
        # RL 训练时的结构是：User Message = [System Prompt] + [Input Template] + [Response:]
        
        # 1. 构造完整的 Prompt 文本 (模拟 Jinja 模板的行为)
        full_user_content = (
            prompt.strip()
            .replace("{{ (prompt or content) | trim }}", user_content.strip())
        )
        
        # 2. LLaMA-Factory 评测格式 (messages)
        # Use explicit system and user roles so evaluation matches RL training structure
        item = {
            "messages": [
                {"role": "system", "content": prompt.strip()},
                {"role": "user", "content": user_content.strip()},
                {"role": "assistant", "content": assertion}
            ],
            "assert": assertion,
            "prefix": pre,
            "focal_method": code,
            "ast_generates": data[i].get('ast_generates', []),
             # 额外的元数据，方便 debug
            "original_system": prompt,
            "original_user": user_content
        }
        
        if i >= int(train_length):
            test_data.append(item)
        else:
            train_data.append(item)

# 确定输出路径
output_dir = os.path.dirname(input_file)
if not output_dir: output_dir = "."

train_out = os.path.join(output_dir, "train_nothink.json")
test_out = os.path.join(output_dir, "test_nothink.json")

with open(train_out, 'w', encoding='utf-8') as f:
    json.dump(train_data, f, ensure_ascii=False, indent=4)

with open(test_out, 'w', encoding='utf-8') as f:
    json.dump(test_data, f, ensure_ascii=False, indent=4)
    
print(f"Saved to {train_out} and {test_out}")
