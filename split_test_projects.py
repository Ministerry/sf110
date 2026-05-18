import json
import random

# 定义项目映射 (从之前的输出中获取)
# 54: jhandballmoves
# 56: jipa
# 57: jiprof
# 69: lotus
# 77: omjstate
# 93: summa
# 103: xbus

REQUIRED_PROJECTS = [
    "jhandballmoves", "jipa", "jiprof", "lotus", "omjstate", "summa", "xbus"
]

def split_test_set():
    with open('items_test_fresh.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 获取所有项目列表
    all_projects = sorted(list(set(item.get("project", "") for item in data)))
    
    # 排除已选中的必须项目
    other_projects = [p for p in all_projects if p not in REQUIRED_PROJECTS]
    
    # 随机选择剩下的 4 个项目 (11 - 7 = 4)
    random.seed(42) # 固定随机种子以便复现
    selected_others = random.sample(other_projects, 4)
    
    final_projects = REQUIRED_PROJECTS + selected_others
    print(f"Selected 11 projects: {final_projects}")

    test_data = []
    train_data = []

    for item in data:
        if item.get("project") in final_projects:
            test_data.append(item)
        else:
            train_data.append(item)

    # 保存测试集
    with open('items_test_selected.json', 'w', encoding='utf-8') as f:
        json.dump(test_data, f, ensure_ascii=False, indent=2)
    
    # 保存剩余的为 SFT 训练集（如果需要）
    with open('items_train_remaining.json', 'w', encoding='utf-8') as f:
        json.dump(train_data, f, ensure_ascii=False, indent=2)

    print(f"Test set size: {len(test_data)} items")
    print(f"SFT candidate set size: {len(train_data)} items")

if __name__ == "__main__":
    split_test_set()
