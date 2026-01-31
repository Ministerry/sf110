import re

path = '/home/fdse/rmy/SF110/96_heal/evosuite-tests/org/heal/module/search/SearchResultBeanEvoSuiteTest.java'

with open(path, 'r') as f:
    content = f.read()

# Remove test7 with more flexible whitespace matching
# Pattern: @Test (whitespace) public void test7() (anything until }) 
content = re.sub(r'@Test\s+public void test7\(\)[\s\S]*?\}', '', content)

with open(path, 'w') as f:
    f.write(content)
