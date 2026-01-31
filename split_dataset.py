import os
import json
import pandas as pd
#打开json文件
with open("qwen_train.json", 
          "r", encoding='utf-8') as file:
    data = json.load(file)

#确定数据集数量和格式
train_data = []
test_data = []
print("data的长度为：", len(data))
input_template = """
# Focal Method:
```java
{focal_method}
```
# Test Prefix:
```java
{prefix}
```
"""
prompt = '''
【角色】
你是Java变异测试断言专家，专门生成能**精确检测代码缺陷**的断言。

【核心任务】
为给定的Java方法和测试上下文生成一个断言，要求：
1. 必须编译通过（语法正确）
2. 必须在原始正确版本中通过
3. 必须在至少一个有缺陷的变异体中失败
4. 尽可能杀死更多的变异体

【思维路径】
1. **锁定目标**：在 test_prefix 中找到 `focal_method` 的调用。断言必须针对**该调用的返回值**或**被修改的对象**。
2. **避坑**：不要调用不存在的 getter 或字段。如果不知道对象结构，优先断言基本类型返回值或使用 `assertEquals(expected, obj)`。
3. **定断言**：设计一个能区分原代码和变异代码（如条件反转、运算错误）的检查。

【严格约束】
1. **目标精准**：绝不要断言 test_prefix 末尾由其他无关方法生成的变量。只关注 focal_method 的产物。
2. **API 谨慎**：严禁臆造不存在的方法（如 `getResult()`），除非你确定它是标准库的一部分。
3. **格式**：只输出一行 Java 代码，无注释/Markdown。
4. **编译性**：变量必须已存在；浮点对比需 delta；数值后缀要准确。

【输入格式】
你将收到：
1. focal_method：原始正确的Java方法，可能带有注释
2. test_prefix：测试上下文（已初始化对象和参数）

【输出要求】
1. 只输出一条可编译的JUnit断言语句，不要其他文本或注释
2. 优先使用 assertEquals / assertArrayEquals / assertThrows 等精确断言，其次才考虑 assertTrue
3. 使用 test_prefix 中已有变量；如需新变量，先在断言前一行声明（否则不要新增）
4. 若断言浮点，提供 delta；若验证异常，使用 assertThrows
5. 断言应区分至少一种典型变异（算术替换、条件翻转、返回常量、off-by-one、空处理缺失），选择能杀死最可能变异的检查点

【示例】
focal_method：
```java
public double calculateDiscount(double price, boolean isMember) {
    double discountRate = isMember ? 0.20 : 0.10;
    return price * (1 - discountRate);
}
```

test_prefix：
```java
PriceCalculator calc = new PriceCalculator();
double finalPrice = calc.calculateDiscount(100.0, true);
```

EXAMPLE_EXCELLENT_OUTPUT：
```java
assertEquals(80.0, finalPrice, 0.001);
```

EXAMPLE_GOOD_OUTPUT：
```java
assertTrue(finalPrice == 80.0);
```

EXAMPLE_BAD_OUTPUT：
```java
assertTrue(finalPrice >= 0); 
```

EXAMPLE_WORST_OUTPUT（绝对不能出现）：
```java
assertTrue(finalPrice = 80.0);
```

'''
train_length = input("请输入数据集数量：")
for i in range(len(data)):
    pre = data[i]['prefix']
    assertion = data[i]['assert']
    code = data[i]['focal_method']
    if len(data[i]['ast_generates']) != 0:
        item = {
            "instruction": prompt,
            # "input": "#Focal Method\n```java\n{" + line['focal_method'] + "}\n```\n#Test Prefix\n```java\n{" + line['prefix'] + "}\n```\n" + "}\n",
            "input" : input_template.format(focal_method = code,prefix = pre),
            "output": assertion
        }
        if i >= int(train_length):
            test_data.append(item)
        else:
            train_data.append(item)

with open("train.json", 'w', encoding='utf-8') as f:
    json.dump(train_data, f, ensure_ascii=False, indent=4)


with open("test.json", 'w', encoding='utf-8') as f:
    json.dump(test_data, f, ensure_ascii=False, indent=4)
