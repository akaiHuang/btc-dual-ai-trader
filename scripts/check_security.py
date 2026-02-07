#!/usr/bin/env python3
"""
安全檢查腳本 - 確保沒有敏感資訊洩漏到 Git
"""

import os
import sys
import re
from pathlib import Path

# 敏感關鍵字模式
SENSITIVE_PATTERNS = [
    r'api[_-]?secret',
    r'secret[_-]?key',
    r'private[_-]?key',
    r'password',
    r'token',
    r'api[_-]?key\s*=\s*["\'](?!YOUR_|your_|test_|example_)',
]

# 需要檢查的文件類型
CHECK_EXTENSIONS = ['.py', '.json', '.yaml', '.yml', '.sh', '.env.example']

# 排除目錄
EXCLUDE_DIRS = {'.git', 'venv', 'env', 'node_modules', '__pycache__', '.pytest_cache'}


def check_file(file_path: Path) -> list:
    """檢查單個文件是否包含敏感資訊"""
    issues = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                # 跳過註解行
                if line.strip().startswith('#'):
                    continue
                    
                for pattern in SENSITIVE_PATTERNS:
                    if re.search(pattern, line, re.IGNORECASE):
                        # 檢查是否是範例值
                        if any(x in line.lower() for x in ['example', 'your_', 'change_me', 'todo']):
                            continue
                        
                        issues.append({
                            'file': str(file_path),
                            'line': line_num,
                            'content': line.strip()[:80]  # 只顯示前 80 字元
                        })
    except Exception as e:
        print(f"⚠️  無法讀取 {file_path}: {e}")
    
    return issues


def scan_repository(root_dir: Path) -> dict:
    """掃描整個倉庫"""
    all_issues = []
    
    for root, dirs, files in os.walk(root_dir):
        # 移除排除的目錄
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        
        for file in files:
            file_path = Path(root) / file
            
            # 檢查文件擴展名
            if file_path.suffix in CHECK_EXTENSIONS or file in ['.env.example']:
                issues = check_file(file_path)
                all_issues.extend(issues)
    
    return all_issues


def check_env_file_exists():
    """檢查 .env 是否存在但未被 git 追蹤"""
    env_path = Path('.env')
    
    if env_path.exists():
        # 檢查 .gitignore 是否包含 .env
        gitignore_path = Path('.gitignore')
        if gitignore_path.exists():
            with open(gitignore_path, 'r') as f:
                if '.env' not in f.read():
                    return False, ".env 存在但未在 .gitignore 中！"
        return True, ".env 已正確被 .gitignore 保護"
    else:
        return False, ".env 文件不存在（請從 .env.example 複製）"


def main():
    print("🔍 開始掃描敏感資訊...")
    print("=" * 60)
    
    # 檢查 .env
    env_ok, env_msg = check_env_file_exists()
    if env_ok:
        print(f"✅ {env_msg}")
    else:
        print(f"⚠️  {env_msg}")
    
    print()
    
    # 掃描倉庫
    root_dir = Path.cwd()
    issues = scan_repository(root_dir)
    
    if issues:
        print(f"❌ 發現 {len(issues)} 個潛在的敏感資訊洩漏：\n")
        for issue in issues:
            print(f"  文件: {issue['file']}")
            print(f"  行號: {issue['line']}")
            print(f"  內容: {issue['content']}")
            print()
        
        print("⚠️  請檢查這些文件並移除敏感資訊！")
        sys.exit(1)
    else:
        print("✅ 沒有發現敏感資訊洩漏")
        print()
        print("建議：")
        print("  1. 將此腳本加入 pre-commit hook")
        print("  2. 定期執行此檢查")
        print("  3. 使用環境變數管理所有敏感資訊")
        sys.exit(0)


if __name__ == '__main__':
    main()
