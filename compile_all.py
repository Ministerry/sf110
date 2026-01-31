import os
import json
import subprocess
import sys
import argparse
import datetime
import re
from pathlib import Path
import time
import traceback
from typing import Optional, Dict, List, Union

root_dir = os.getcwd()
project_dirs = [d for d in os.listdir(root_dir)
                if os.path.isdir(os.path.join(root_dir, d))]
idx = 0
for project_dir in project_dirs:
    result: Dict = {}
    project_path = os.path.join(root_dir, project_dir)
    parts = project_dir.split('_')
    if "108" in project_dir:
        continue
    if len(parts) != 2:
        continue
    print(project_path)
    import shutil
    src_resources_dir = os.path.join(project_path, 'src/main/resources')
    dest_classes_dir = os.path.join(project_path, 'target/classes')
    
    if os.path.exists(src_resources_dir):
        os.makedirs(dest_classes_dir, exist_ok=True)
        for item in os.listdir(src_resources_dir):
            src_path = os.path.join(src_resources_dir, item)
            dest_path = os.path.join(dest_classes_dir, item)
            if os.path.isfile(src_path):
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copy2(src_path, dest_path)
            elif os.path.isdir(src_path):
                if os.path.exists(dest_path):
                    shutil.rmtree(dest_path)
                shutil.copytree(src_path, dest_path)
    else:
        print(f"Source directory does not exist: {src_resources_dir}")
        
    cmd = ['bash' , 'compile.sh']
    try:
        result = subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10000
        )
        print(result.stdout)
    except subprocess.TimeoutExpired:
        print(f"[WARN] 命令超时({100}s): {' '.join(cmd)}")
    except Exception as e:
        print(f"[ERROR] 命令执行异常: {str(e)}")
        

        # 96 - 23 - 109 - 86 - 52 - 61 - 79 - 104 - 102 - 54 -15 -92 - 107