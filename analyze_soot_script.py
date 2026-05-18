import json

with open('/home/ubuntu/myren/SF110/rl_test_end.json') as f:
    data = json.load(f)

for item in data:
    if 'soot_analysis_result' in item and item['soot_analysis_result']:
        print(json.dumps(item['soot_analysis_result']['variables'], indent=2))
        break
