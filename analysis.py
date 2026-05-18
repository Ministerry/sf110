import os
import json
import sys
improve = 0
back = 0
same = 0
result = []
model_1 = sys.argv[1]

model_2 = sys.argv[2]
with open(f"excution_{model_1}_generated_predictions.json","r",encoding="utf-8") as f:
    data_before = json.load(f)

with open(f"excution_{model_2}_generated_predictions.json","r",encoding="utf-8") as f:
    data_after = json.load(f)

with open("qwen_test.json","r",encoding="utf-8") as f:
    data_ground = json.load(f)

for i in range(len(data_before)):
    result.append({"focal_method" : data_before[i]['focal_method'],
                   f"{model_1}" : data_before[i]['reward'],
                   f"{model_2}" : data_after[i]['reward'],
                   f"predict_{model_1}" : data_before[i]['predict'],
                   f"predict_{model_2}" : data_after[i]['predict'],
                   "prefix": data_ground[i]['prefix'],
                   "predict_data_ground" : data_ground[i]['assert'],
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