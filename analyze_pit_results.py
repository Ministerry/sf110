import json
import os

files = [
    '/home/ubuntu/myren/SF110/excution_qwen2.5_1.5b_origin_pit_evaluation_results.json',
    '/home/ubuntu/myren/SF110/excution_qwen2.5_1.5b_after_pit_evaluation_results.json',
    '/home/ubuntu/myren/SF110/excution_qwen3_8b_instruct_origin_4_6_pit_evaluation_results.json',
    '/home/ubuntu/myren/SF110/excution_qwen3_8b_instruct_after_4_6_pit_evaluation_results.json',
    '/home/ubuntu/myren/SF110/excution_deepseek_pit_evaluation_results.json',
    '/home/ubuntu/myren/SF110/excution_evosuite_pit_evaluation_results.json'
]

results = []

for file_path in files:
    if not os.path.exists(file_path):
        results.append({"file": os.path.basename(file_path), "error": "File not found"})
        continue
    
    with open(file_path, 'r') as f:
        try:
            data = json.load(f)
        except Exception as e:
            results.append({"file": os.path.basename(file_path), "error": str(e)})
            continue
    
    total_items = len(data)
    items_with_kill = sum(1 for item in data if item.get('pit_kill_count', 0) > 0)
    total_mutants = sum(item.get('pit_total_mutants', 0) for item in data)
    total_killed = sum(item.get('pit_kill_count', 0) for item in data)
    overall_kill_rate = (total_killed / total_mutants * 100) if total_mutants > 0 else 0
    avg_kill_rate = sum(item.get('pit_kill_rate', 0) for item in data) / total_items if total_items > 0 else 0
    
    results.append({
        "file": os.path.basename(file_path),
        "total_items": total_items,
        "items_with_kill": items_with_kill,
        "total_mutants": total_mutants,
        "total_killed": total_killed,
        "overall_kill_rate": overall_kill_rate,
        "avg_kill_rate": avg_kill_rate
    })

print(json.dumps(results, indent=2))
