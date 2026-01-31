import os
import json
import pandas as pd
#打开json文件
with open("bug_test.json", 
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

【生成策略 - 分步思考】
当看到方法时，按此流程思考：
1. **识别关键计算点**：方法中哪部分计算最容易出错？
2. **分析缺陷注入点**：如果我是变异工具，会在哪里修改代码？
3. **设计针对性检查**：什么断言能检测到这个修改？
4. **验证有效性**：这个断言在正确版本中会通过吗？

【断言质量优先级】
按此顺序考虑：
1. 编译正确性（绝不能有语法错误）
2. 区分能力（必须杀死至少一个变异体）
3. 精确性（优先使用assertEquals而非assertTrue）
4. 覆盖范围（一个断言检测多个缺陷更好）

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

【生成策略 - 思考链】
1. 找核心不变量/边界：哪些输出或副作用最能体现业务正确性？
2. 识别潜在变异：算术/条件/返回值/边界/空值/集合大小与顺序/异常分支。
3. 选择断言形式：能精确暴露该变异且在正确版本通过。
4. 编译自检：语法合法；数值字面量带 d/f；浮点断言含 delta；无赋值代替比较。

【关键提醒】
1. 生成的断言必须是有效的Java代码
2. 断言应该针对方法的业务逻辑，而非无关细节
3. 考虑边界情况和极端值
4. 使用具体的预期值而非模糊判断

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
