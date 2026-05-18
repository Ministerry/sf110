import os
import json
import xml.etree.ElementTree as ET

def parse_pit_xml(xml_path):
    """
    解析 PITest 的 mutations.xml 文件
    """
    if not os.path.exists(xml_path):
        return []
    
    mutants = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        for mutation in root.findall('mutation'):
            # 提取 PITest 关键字段，增加安全检查
            mutat_method = mutation.find('mutatedMethod')
            line_num = mutation.find('lineNumber')
            mutator = mutation.find('mutator')
            desc = mutation.find('description')
            mutated_class = mutation.find('mutatedClass')
            
            mutant = {
                "id": str(len(mutants) + 1),
                "mutator": mutator.text.split('.')[-1] if mutator is not None and mutator.text else "Unknown",
                "method": mutat_method.text if mutat_method is not None else "Unknown",
                "line": line_num.text if line_num is not None else "0",
                "description": desc.text if desc is not None else "",
                "status": mutation.get('status', 'UNKNOWN'),
                "mutated_class": mutated_class.text if mutated_class is not None else ""
            }
            mutants.append(mutant)
    except Exception as e:
        print(f"[ERROR] Failed to parse {xml_path}: {e}")
        
    return mutants

def main(input_json, output_json):
    with open(input_json, 'r', encoding='utf-8') as f:
        data = json.load(f)

    CWD = "/home/ubuntu/myren/SF110"
    total_mutants = 0

    for item in data:
        project = item.get('project')
        bug_num = item.get('bug_num')
        
        # 确定项目路径（处理文件夹命名差异）
        project_dir_name = f"{bug_num}_{project}"
        project_path = os.path.join(CWD, project_dir_name)
        if not os.path.isdir(project_path):
            project_path = os.path.join(CWD, f"{project}_{bug_num}")
        
        xml_report = os.path.join(project_path, "pit_reports", "mutations.xml")
        
        if os.path.exists(xml_report):
            pit_mutants = parse_pit_xml(xml_report)
            # 存入 json，格式参考 ast_generates
            item['pit_generates'] = pit_mutants
            total_mutants += len(pit_mutants)
            print(f"[INFO] {project}_{bug_num}: Collected {len(pit_mutants)} mutants.")
        else:
            item['pit_generates'] = []

    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    
    print(f"\n[DONE] Saved {total_mutants} mutants to {output_json}")

if __name__ == "__main__":
    main("/home/ubuntu/myren/SF110/qwen_test.json", "/home/ubuntu/myren/SF110/qwen_test_with_pit.json")
