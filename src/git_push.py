# src/git_push.py
# 每次生成报表后自动 git push 到 GitHub
# GitHub Pages 会在几分钟内自动更新

import subprocess
from pathlib import Path
# src/git_push.py — 改 REPO_ROOT 指向博客仓库
from config_loader import load_config

def push_reports(date_str: str):
    config = load_config()
    output_dir = config.get("settings", {}).get("output_dir")
    repo_root = Path(output_dir).parent if output_dir else Path(__file__).parent.parent

    try:
        _run(["git", "add", "trading/"], cwd=repo_root)
        result = subprocess.run(
            ["git", "commit", "-m", f"trading report: {date_str}"],
            cwd=repo_root, capture_output=True, text=True
        )
        if "nothing to commit" in result.stdout + result.stderr:
            print("  git: 无新变更")
            return
        _run(["git", "push", "origin", "main"], cwd=repo_root)
        print("  已推送到 GitHub")
    except Exception as e:
        print(f"  git push 失败: {e}")

def _run(cmd, cwd):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())