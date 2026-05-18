import json
import random
import os
import re
import hashlib

# 默认配置路径（可在主入口指定其它文件）
DEFAULT_RL_PATH = "/home/ubuntu/myren/SF110/rl_havedoc.json"
DEFAULT_SFT_PATH = "/home/ubuntu/myren/SF110/sft_nodoc.json"


def split_file(input_path: str, train_out: str, test_out: str, val_out: str, seed: int | None = None):
    """Split a single assembled file by prefix into train/test/val (approx 80/10/10).

    Groups are formed by `prefix` (or `extra_info.prefix`), and an entire group
    is assigned to exactly one split to avoid leakage.
    """
    if seed is None:
        seed = int(os.getenv("SPLIT_SEED", "42"))
    random.seed(seed)

    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Assembled file not found: {input_path}")

    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Assembled data must be a list of samples")

    # group samples by prefix (with special handling for generic/undeclared prefixes)
    prefix_map = {}
    for item in data:
        if isinstance(item, dict):
            raw_p = item.get('prefix')
            if raw_p is None:
                ei = item.get('extra_info') or {}
                raw_p = ei.get('prefix')
            if raw_p is None:
                raw_p = ""
            p_norm = raw_p.strip() if isinstance(raw_p, str) else ""

            # Treat highly generic or empty prefixes specially to avoid one huge group.
            is_generic = (p_norm == "" or 'Undeclared exception' in p_norm)
            if is_generic:
                # build a composite key including focal_method and test path to differentiate contexts
                focal = item.get('focal_method') or (item.get('extra_info') or {}).get('focal_method') or ''
                test_path = item.get('test_method_path') or (item.get('extra_info') or {}).get('test_method_path') or ''
                m = re.search(r'Undeclared exception!\s*(\w+)', p_norm)
                exname = m.group(1) if m else ''
                composite = f"{p_norm}||{exname}||{focal}||{test_path}"
                # shorten composite into a stable hash to use as group key
                p = hashlib.sha1(composite.encode('utf-8')).hexdigest()
            else:
                p = p_norm
        else:
            p = ""
        prefix_map.setdefault(p, []).append(item)

    prefixes = list(prefix_map.keys())
    random.shuffle(prefixes)

    total = len(data)
    train_target = total * 0.8
    test_target = total * 0.9

    train_data = []
    test_data = []
    val_data = []
    train_count = 0
    test_count = 0

    for p in prefixes:
        group = prefix_map[p]
        g_len = len(group)
        if train_count < train_target:
            train_data.extend(group)
            train_count += g_len
        elif train_count + test_count < test_target:
            test_data.extend(group)
            test_count += g_len
        else:
            val_data.extend(group)

    # Safety: if val empty, move a small portion from train
    if not val_data and train_data:
        move_n = max(1, int(len(train_data) * 0.1))
        val_data = train_data[-move_n:]
        train_data = train_data[:-move_n]

    os.makedirs(os.path.dirname(train_out) or '.', exist_ok=True)

    with open(train_out, 'w', encoding='utf-8') as f:
        json.dump(train_data, f, indent=2, ensure_ascii=False)
    with open(test_out, 'w', encoding='utf-8') as f:
        json.dump(test_data, f, indent=2, ensure_ascii=False)
    with open(val_out, 'w', encoding='utf-8') as f:
        json.dump(val_data, f, indent=2, ensure_ascii=False)

    # return metadata for validation
    def prefixes_of(lst):
        s = set()
        for it in lst:
            if isinstance(it, dict):
                p = it.get('prefix')
                if p is None:
                    p = (it.get('extra_info') or {}).get('prefix') or ''
            else:
                p = ''
            s.add(p)
        return s

    return {
        'total': total,
        'train_n': len(train_data),
        'test_n': len(test_data),
        'val_n': len(val_data),
        'train_prefixes': prefixes_of(train_data),
        'test_prefixes': prefixes_of(test_data),
        'val_prefixes': prefixes_of(val_data),
    }



def split_dataset(seed: int | None = None):
    # Backwards-compatible entry: run splits for both default files if present
    if seed is None:
        seed = int(os.getenv("SPLIT_SEED", "42"))

    tasks = []
    if os.path.exists(DEFAULT_SFT_PATH):
        tasks.append((DEFAULT_SFT_PATH,
                      "/home/ubuntu/myren/SF110/sft_train.json",
                      "/home/ubuntu/myren/SF110/sft_test.json",
                      "/home/ubuntu/myren/SF110/sft_val.json"))
    if os.path.exists(DEFAULT_RL_PATH):
        tasks.append((DEFAULT_RL_PATH,
                      "/home/ubuntu/myren/SF110/rl_train.json",
                      "/home/ubuntu/myren/SF110/rl_test.json",
                      "/home/ubuntu/myren/SF110/rl_val.json"))

    if not tasks:
        raise FileNotFoundError("No default input files found (sft_nodoc.json or rl_havedoc.json)")

    for inp, tr, te, va in tasks:
        print(f"\nProcessing: {inp} -> {tr}, {te}, {va}")
        meta = split_file(inp, tr, te, va, seed=seed)
        print(f"总样本: {meta['total']} | 训练: {meta['train_n']} | 测试: {meta['test_n']} | 验证: {meta['val_n']}")
        # verify prefix disjointness
        tp = meta['train_prefixes']
        qp = meta['test_prefixes']
        vp = meta['val_prefixes']
        inter_tq = tp & qp
        inter_tv = tp & vp
        inter_qv = qp & vp
        print(f"prefix 交集 (train∩test): {len(inter_tq)} ; (train∩val): {len(inter_tv)} ; (test∩val): {len(inter_qv)}")
        # show some example prefixes


    # Post-split cleaning when both SFT and RL outputs exist
    # Goal: ensure SFT_train has no prefix overlap with any RL split,
    # and optionally remove SFT focal_methods that appear in RL val/test.
    sft_train = "/home/ubuntu/myren/SF110/sft_train.json"
    rl_train = "/home/ubuntu/myren/SF110/rl_train.json"
    rl_val = "/home/ubuntu/myren/SF110/rl_val.json"
    rl_test = "/home/ubuntu/myren/SF110/rl_test.json"
    sft_clean_out = "/home/ubuntu/myren/SF110/sft_train_clean.json"
    sft_final_out = "/home/ubuntu/myren/SF110/sft_train_final.json"

    if os.path.exists(sft_train) and os.path.exists(rl_train):
        # load sets
        def load(path):
            with open(path,'r',encoding='utf-8') as f:
                return json.load(f)

        sft_data = load(sft_train)
        rl_data = load(rl_train)
        rl_val_data = load(rl_val) if os.path.exists(rl_val) else []
        rl_test_data = load(rl_test) if os.path.exists(rl_test) else []

        def get_prefix(it):
            if isinstance(it, dict):
                return it.get('prefix') or (it.get('extra_info') or {}).get('prefix') or ''
            return ''

        def get_focal(it):
            if isinstance(it, dict):
                return it.get('focal_method') or (it.get('extra_info') or {}).get('focal_method') or ''
            return ''

        rl_prefixes = set(get_prefix(x) for x in rl_data) | set(get_prefix(x) for x in rl_val_data) | set(get_prefix(x) for x in rl_test_data)

        # Samples in SFT whose prefix overlaps RL: move them into RL train and remove from SFT
        sft_overlap = [x for x in sft_data if get_prefix(x) in rl_prefixes]
        sft_no_prefix_overlap = [x for x in sft_data if get_prefix(x) not in rl_prefixes]
        removed_by_prefix = len(sft_overlap)

        if sft_overlap:
            # Do not append to RL. Save overlapping SFT samples to a backup and remove from SFT.
            moved_out = "/home/ubuntu/myren/SF110/sft_overlap_removed.json"
            with open(moved_out, 'w', encoding='utf-8') as f:
                json.dump(sft_overlap, f, indent=2, ensure_ascii=False)
            print(f"Removed {len(sft_overlap)} SFT samples (by prefix) and saved backup to: {moved_out}")
        else:
            print("No SFT prefixes overlapped RL prefixes; no movement needed.")

        # save intermediate SFT without RL-prefix samples
        with open(sft_clean_out, 'w', encoding='utf-8') as f:
            json.dump(sft_no_prefix_overlap, f, indent=2, ensure_ascii=False)

        # Next: remove any samples whose focal_method appears in RL val/test (stronger semantic isolation)
        rl_val_test_focals = set(get_focal(x) for x in rl_val_data) | set(get_focal(x) for x in rl_test_data)
        sft_final = [x for x in sft_no_prefix_overlap if get_focal(x) not in rl_val_test_focals]
        removed_by_focal = len(sft_no_prefix_overlap) - len(sft_final)
        with open(sft_final_out, 'w', encoding='utf-8') as f:
            json.dump(sft_final, f, indent=2, ensure_ascii=False)

        print(f"\nPost-split cleaning summary for SFT:")
        print(f"original sft_train: {len(sft_data)} | after moving overlapping prefixes: {len(sft_no_prefix_overlap)} (moved {removed_by_prefix}) | after focal removal: {len(sft_final)} (removed {removed_by_focal})")
        if removed_by_prefix or removed_by_focal:
            print(f"clean files: {sft_clean_out}, {sft_final_out}")

if __name__ == "__main__":
    split_dataset()
