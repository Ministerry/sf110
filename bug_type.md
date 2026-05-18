Bug大类
对应方法
描述
具体变异算子 (Mutation Operators)
Null Reference Failures(空指针异常)

nullinject
制造空指针引用或移除空值防御机制

1. Guard Removal: 移除 if(x == null) 检查2. Unwrap Guard: 展开 if(x != null) 块（无条件执行）3. Null Assignment: 将变量初始化或赋值改为 null4. Require Check Removal: 移除 Objects.requireNonNull(x)
Index Boundary Failures(索引越界)
Indexinject
破坏数组/集合的索引边界条件
1. Condition Weakening: i < N -> i <= N (For循环/If)2. Access Offset: 数组/List访问 arr[i] -> arr[i+1] / arr[i-1]3. Allocation Size: 数组创建 new T[N] -> new T[N-1]4. Method Arg: substring(i) -> substring(i+1)
Resource Management Failures(资源泄露)
resouceinject
破坏资源的正常关闭与异常处理流程
1. TWR Unwrap: 展开 try-with-resources 为普通块（失去自动关闭）2. Close Removal: 删除显式调用的 .close() 方法3. Catch Swallow: 清空 catch 块或泛化异常类型（忽略错误）
Concurrent Modification Failures(并发/死循环)
concurrentinject
破坏线程安全或制造死循环
1. Sync Removal: 移除 synchronized 关键字或代码块2. Thread Start: thread.start() -> thread.run() (同步执行)3. Wait/Notify: 移除 wait(), notify(), lock() 调用4. Infinite Loop: 制造 while(true) 或 for(;;) 死循环5. CME: 在遍历循环中插入集合修改操作 (add/remove)
Incorrect Behavior Failures(计算/行为错误)
incorrectinject
改变计算结果或赋值逻辑（非控制流错）
1. Calc Op Swap: 结果计算中的 + <-> -, * <-> / 等2. Assign Op Swap: += <-> -=, *= <-> /=3. Math Swap: Math.min <-> Math.max, floor <-> ceil4. Arg Swap: 交换方法调用的前两个参数 func(a, b) -> func(b, a)5. Unary Swap: i++ <-> i--
Logic Assertion Failures(逻辑断言失败)
logicinject
破坏布尔逻辑和控制流条件
1. Relational Swap: > <-> >=, < <-> <=, == <-> !=2. Logical Swap: && <-> `
Data Integrity Failures(数据完整性)
datainject
破坏数据校验或引入非法数据状态
1. Zero/Neg Check Removal: 移除 x > 0, x != 0 的校验2. Div By Zero: 将除数替换为0 (x / y -> x / 0)3. Empty Check Removal: 移除 isEmpty() 或 size()==0 的校验4. Literal Swap: 常用数值字面量改为 0 或负数
Numeric Computation Failures(数值计算错误)
numericinject
引入数值溢出或精度问题
1. Integer Overflow: 字面量/操作数 -> Integer.MAX_VALUE / MIN_VALUE2. Float Precision: 字面量 -> NaN / Infinity / 1e3083. Div/Mod Swap: / <-> % (整数运算逻辑改变)
String Processing Failures(字符串处理)

stringinject

破坏字符串操作的语义
1. Equality: equals() -> == (引用比较)2. Case Sensitivity: equalsIgnoreCase -> equals3. Preprocessing: 移除 trim(), toLowerCase() 等4. Regex: matches("regex") -> matches(".*") (绕过校验)5. Encoding: UTF-8 -> ISO-8859-1 (乱码)6. Split: 修改分隔符或移除 split 操作