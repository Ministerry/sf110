import re

path = '/home/fdse/rmy/SF110/96_heal/evosuite-tests/org/heal/module/search/SearchResultBeanEvoSuiteTest.java'

with open(path, 'r') as f:
    content = f.read()

# Remove test1 and test7
content = re.sub(r'  @Test\n  public void test1\(\)  throws Throwable  \{[\s\S]*?\}\n\n', '', content)
content = re.sub(r'  @Test\n  public void test7\(\)  throws Throwable  \{[\s\S]*?\}\n\n', '', content)

with open(path, 'w') as f:
    f.write(content)
