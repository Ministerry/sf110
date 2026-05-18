from openai import OpenAI, APIConnectionError
from config import DEEPSEEK_API_KEY
import requests
import json
import pandas as pd
import time
import os
import re
import httpx 
import javalang

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

system_prompt = '''
You are a Java Code Verification Expert, specialized in generating precise assertions based on **Data Flow, State Changes, and Path Constraints**.

【Core Task】
Generate a highly precise assertion for the given Java method and test context.
1. Must compile (syntactically correct).
2. Must originally pass.
3. Must precisely target the exact variables modified by or returned from the `focal_method`.
4. Reflect the core logical constraints (e.g., if/else bounds, math logic) of the focal method.

【Thinking Path】
1. **Analyze Focal Method**: Understand the control flow (conditions, loops), return values, and side effects. Identify what logical boundaries (like > 0, == null) are essential.
2. **Trace Test Context**: Track the object state and variables in `test_prefix`. Find exactly which variable receives the output of `focal_method` or which object is mutated.
3. **Lock Target Variables**: 
   - Ensure you ONLY assert on the variables directly affected (returned or modified) by the `focal_method`.
4. **Design Logic-Aligned Assertion**: 
   - Avoid weak assertions like `assertNotNull`. 
   - Align with internal constraints: If the method does conditional math `if (x > 5)`, assert the exact expected numeric boundary or outcome.
   - For void methods, assert the highly specific mutated object properties.

【Strict Constraints】
1. **Target Precision**: Never assert on unrelated setup variables. Focus ONLY on the direct products/mutations of `focal_method`.
2. **API Caution**: Do not fabricate non-existent methods unless you are absolutely sure they exist.
3. **Format**: Wrap the assertion statement in a Markdown code block, i.e., ```java ... ```.
4. **Compilability**: Variables must already exist; precise types (e.g., float delta, double) must be respected.

【Input Format】
You will receive:
1. focal_method: The original Java method
2. test_prefix: Test context (initialized objects and parameters)

【Output Requirements】
1. Output a compilable JUnit assertion wrapped in a Java code block.
2. Prefer strict equality (`assertEquals`, `assertArrayEquals`) or logical bounds (`assertTrue(var > X)`) that match the method's internal control flow.
3. Include delta for floating point comparisons.
4. The assertion MUST capture the unique behavior and variable state changes specific to this execution path.
'''


data_path = "/home/ubuntu/myren/SF110/qwen_test.json"
with open(data_path,'r',encoding='utf-8') as f:
    data = json.load(f)

results = []


def create_client():
    return OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com",
        timeout=60.0
    )


def model_judge(prompt: str, model="deepseek-chat", temperature=0.0, timeout=60.0):
    """Call the DeepSeek chat model with `prompt` and try to return a parsed JSON/dict or numeric score.
    Returns: dict | float | str (raw)"""
    client = create_client()
    messages = [{"role": "user", "content": prompt}]
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True
        )
        full_response = ""
        for chunk in response:
            # streaming deltas may not have content
            content = getattr(chunk.choices[0].delta, 'content', None)
            if content is not None:
                full_response += content

        full_response = full_response.strip()

        # try extract first JSON object
        m = re.search(r'(\{[\s\S]*\})', full_response)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass

        # try parse float
        try:
            return float(full_response.strip())
        except Exception:
            return full_response
    finally:
        try:
            client.close()
        except Exception:
            pass


success_count = 0
failed_count = 0
skipped_count = 0
SAVE_INTERVAL = 100  # 每处理 100 条保存一次

# 处理所有数据项
if __name__ == '__main__':
    client = create_client()
    id = 0
    for i in range(len(data)):
        print(f"Processing item {i+1}/{len(data)}")

        #  添加过滤逻辑
        focal_method = data[i]['focal_method']
        
        if (i + 1) % SAVE_INTERVAL == 0:
            with open("test_deepseek.json", 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=4)
            print(f" 进度已保存 ({i+1}/{len(data)})")
        try:
            input_text = input_template.format(focal_method=data[i]['focal_method'], prefix=data[i]['prefix'])
            full_user_content = (
                system_prompt.strip() 
                + "\n\n" 
                + input_text.strip() 
                + " The final answer MUST BE a single line of Java code inside a java code block.\n\nResponse:"
            )
            messages = [
                    {"role": "user", "content": full_user_content}
            ]
            
            # 添加重试机制
            max_retries = 3
            retry_delay = 1
            
            for attempt in range(max_retries):
                try:
                    # 使用流式传输
                    response = client.chat.completions.create(
                        model="deepseek-chat",
                        messages=messages,
                        temperature = 0.0,
                        stream=True
                    )
                    
                    # 收集流式响应
                    full_response = ""
                    for chunk in response:
                        content = getattr(chunk.choices[0].delta, 'content', None)
                        if content is not None:
                            full_response += content
                    
                    # 检查响应是否为空
                    if not full_response.strip():
                        raise ValueError("Received empty response from API")
                    full_response = full_response.strip()

                    # 提取所有代码块
                    extracted_assertions = []
                    code_matches = re.finditer(r"```java\s*(.*?)\s*```", full_response, re.DOTALL)
                    
                    for match in code_matches:
                        code_block = match.group(1).strip()
                        if code_block:
                            cleaned_lines = [line for line in code_block.split('\n') if not line.strip().startswith('//')]
                            cleaned_block = '\n'.join(cleaned_lines).strip()
                            if not cleaned_block:
                                continue
                            lines = [line.strip() for line in cleaned_block.split('\n') if line.strip()]
                            if len(lines) > 1 and all(l.startswith('assert') for l in lines):
                                for line in lines:
                                    extracted_assertions.append(line)
                            else:
                                extracted_assertions.append(cleaned_block)

                    seen = set()
                    unique_assertions = []
                    for a in extracted_assertions:
                        if a not in seen:
                            seen.add(a)
                            unique_assertions.append(a)
                    extracted_assertions = unique_assertions[:5]

                    if not extracted_assertions:
                        clean_text = re.sub(r"<think>.*?</think>", "", full_response, flags=re.DOTALL).strip()
                        if clean_text:
                            extracted_assertions.append(clean_text)

                    data[i]["status"] = "success"
                    data[i]["ds_generates"] = extracted_assertions
                    success_count += 1
                    data[i]['id'] = id
                    id += 1
                    results.append(data[i])
                    break
                    
                except APIConnectionError as e:
                    if attempt < max_retries - 1:
                        print(f"  Attempt {attempt + 1} failed: {e}")
                        print(f"  Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        print(f"  Failed after {max_retries} attempts.")
                        data[i]["status"] = "failed"
                        data[i]["ds_generates"] = []
                        failed_count += 1
                # removed specific json error handling
                except Exception as e:
                    print(f"  Unexpected error: {e}")
                    data[i]["status"] = "failed"
                    data[i]["ds_generates"] = []
                    failed_count += 1
                    break
            
            # 添加延迟避免触发限流
            time.sleep(0.5)
            
        except Exception as e:
            print(f"Error processing item {i}: {e}")
            failed_count += 1
            continue

    try:
        client.close()
    except Exception:
        pass

    with open('test_deepseek.json','w',encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)

    print(f"Processing completed. Success: {success_count}, Failed: {failed_count}")
