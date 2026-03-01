import matplotlib.pyplot as plt
import json
import numpy as np
import math
import sys
def load_rewards(path):
    with open(path, "r", encoding="utf-8") as f:
        items = json.load(f)
    data = []
    for it in items:
        if "reward" in it:
            data.append(it["reward"])
        else:
            data.append(-1)
    return data

# data_before = load_rewards("excution_qwen2.5_1.5b_before_generated_predictions.json")
# data_after = load_rewards("excution_qwen2.5_1.5b_after_generated_predictions.json")
# counts_before, bins = np.histogram(data_before, bins=30)
# counts_after, _ = np.histogram(data_after, bins=bins)
# max_count = 0
# if counts_before.size:
#     max_count = max(max_count, int(counts_before.max()))
# if counts_after.size:
#     max_count = max(max_count, int(counts_after.max()))

# ymax = int(math.ceil(max_count * 1.1)) if max_count > 0 else 1

# # plot before
# plt.figure()
# plt.hist(data_before, bins=bins, color='red', edgecolor='black', alpha=0.7)
# plt.title('qwen2.5_1.5b_before:' + str(len(data_before)))
# plt.xlabel('Value')
# plt.ylabel('Frequency')
# plt.grid(True, linestyle='-', alpha=0.3)
# plt.ylim(0, ymax)
# plt.savefig('qwen2.5_1.5b_before.png')
# plt.close()
# print("Graph saved to histogram_before.png")

# # plot after
# plt.figure()
# plt.hist(data_after, bins=bins, color='lightblue', edgecolor='black', alpha=0.7)
# plt.title('qwen2.5_1.5b_after:' + str(len(data_after)))
# plt.xlabel('Value')
# plt.ylabel('Frequency')
# plt.grid(True, linestyle='-', alpha=0.3)
# plt.ylim(0, ymax)
# plt.savefig('qwen2.5_1.5b_after.png')
# plt.close()
# print("Graph saved to histogram_after.png")
model = sys.argv[1]

data_before = load_rewards(f"excution_{model}_before_generated_predictions.json")
data_after = load_rewards(f"excution_{model}_after_generated_predictions.json")
counts_before, bins = np.histogram(data_before, bins=30)
counts_after, _ = np.histogram(data_after, bins=bins)
max_count = 0
if counts_before.size:
    max_count = max(max_count, int(counts_before.max()))
if counts_after.size:
    max_count = max(max_count, int(counts_after.max()))

ymax = int(math.ceil(max_count * 1.1)) if max_count > 0 else 1

# plot before
plt.figure()
plt.hist(data_before, bins=bins, color='yellow', edgecolor='black', alpha=0.7)
plt.title(f'{model}_before:' + str(len(data_before)))
plt.xlabel('Value')
plt.ylabel('Frequency')
plt.grid(True, linestyle='-', alpha=0.3)
plt.ylim(0, ymax)
plt.savefig(f'{model}_before.png')
plt.close()
print("Graph saved to histogram_before.png")

#plot after
plt.figure()
plt.hist(data_after, bins=bins, color='lightblue', edgecolor='black', alpha=0.7)
plt.title(f'{model}_after:' + str(len(data_after)))
plt.xlabel('Value')
plt.ylabel('Frequency')
plt.grid(True, linestyle='-', alpha=0.3)
plt.ylim(0, ymax)
plt.savefig(f'{model}_after.png')
plt.close()
print("Graph saved to histogram_after.png")