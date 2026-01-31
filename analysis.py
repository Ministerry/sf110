import os
import json

improve = 0
back = 0
same = 0
result = []
with open("excution_qwen3_4b_before_generated_predictions.json","r",encoding="utf-8") as f:
    data_before = json.load(f)

with open("excution_qwne3_4b_after_generated_predictions.json","r",encoding="utf-8") as f:
    data_after = json.load(f)

for i in range(len(data_before)):
    result.append({"focal_method" : data_before[i]['focal_method'],
                   "reward_before" : data_before[i]['reward'],
                   "reward_after" : data_after[i]['reward'],
                   "predict_before" : data_before[i]['predict'],
                   "predict_after" : data_after[i]['predict'],
    })
    if data_before[i]['reward'] > data_after[i]['reward']:
        back += 1
    if data_before[i]['reward'] < data_after[i]['reward']:
        improve += 1
    if data_before[i]['reward'] == data_after[i]['reward']:
        same += 1

print(improve,back,same)

with open('result.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)