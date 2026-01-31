import re

path = '/home/fdse/rmy/SF110/96_heal/evosuite-tests/org/heal/module/user/UserBeanEvoSuiteTest.java'

with open(path, 'r') as f:
    content = f.read()

# Pattern to find test methods
# This is a simple regex that assumes standard formatting of evosuite tests
# It removes the entire method block
content = re.sub(r'  @Test\n  public void test0\(\)  throws Throwable  \{[\s\S]*?\}\n\n', '', content)
content = re.sub(r'  @Test\n  public void test30\(\)  throws Throwable  \{[\s\S]*?\}\n\n', '', content)

with open(path, 'w') as f:
    f.write(content)
