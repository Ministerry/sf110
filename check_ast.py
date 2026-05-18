import json
from collections import Counter

try:
    with open('rl_val_end.json','r',encoding='utf-8') as f: 
        data = json.load(f)
    print(f"Total items: {len(data)}")
    items = []
    total_ast = 0
    count_5 = 0
    count_10 = 0
    count_20 = 0
    bug_types_counter = Counter()

    excluded_mutations = {
        "literal_to_int_max",
        "literal_to_int_min",
        "literal_to_zero",
        "literal_to_negative_one",
        "literal_to_int_max",
        "literal_to_int_min",
        "make_for_infinite", 
        "make_while_infinite", 
        "make_do_while_infinite", 
        "remove_wait_timeout"
    }

    for item in data:
        original_variants = item.get('ast_generates', [])
        # 过滤掉容易导致无限循环的变体
        filtered_variants = [v for v in original_variants if v.get('mutation') not in excluded_mutations]
        
        ast_len = len(filtered_variants)
        
        if ast_len >= 5: 
            item['ast_generates'] = filtered_variants
            total_ast += ast_len
            count_5 += 1
            items.append(item) 
            
            for v in filtered_variants:
                bug_types_counter[v.get('bug_type', 'Unknown')] += 1
                
            if ast_len > 10: count_10 += 1
            if ast_len > 20: count_20 += 1

    avg = total_ast / len(items) if len(items) > 0 else 0

    print(f"Average valid ast_generates per kept item: {avg:.2f}")
    print(f"Items with >= 5 valid ast_generates: {count_5}")
    print(f"Items with > 10 ast_generates: {count_10}")
    print(f"Items with > 20 ast_generates: {count_20}")
    
    print("\nBug type statistics across valid kept variants:")
    for b_type, count in bug_types_counter.most_common():
        print(f"  {b_type}: {count}")
    print()

    with open('rl_val_test_filter.json', 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"Filtered items saved: {len(items)}")
except Exception as e:
    print("Error:", e)
