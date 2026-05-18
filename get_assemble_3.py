import json

result = []
with open('rl_train_test_filter_1.json','r',encoding='utf-8') as f: 
    data = json.load(f)
for i in range(0,1000):
    result.append(data[i])

with open('rl_train_test_filter_2.json','r',encoding='utf-8') as f: 
    data = json.load(f)
for i in range(1000,2000):
    result.append(data[i])

with open('rl_train_test_filter_3.json','r',encoding='utf-8') as f: 
    data = json.load(f)
for i in range(2000,len(data)):
    result.append(data[i])

with open('rl_train_test_filter_end.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(len(result))