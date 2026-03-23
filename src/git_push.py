# src/git_push.py
# 每次生成报表后自动 git push 到 GitHub
# GitHub Pages 会在几分钟内自动更新

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def push_reports(date_str: str):
    """
    仅推送 docs/ 目录（报表文件），不推送数据库。
    如果没有新变更则静默跳过。
    """
    try:
        # 1. git add docs/
        _run(["git", "add", "docs/"])

        # 2. git commit
        result = subprocess.run(
            ["git", "commit", "-m", f"report: {date_str}"],
            cwd=REPO_ROOT, capture_output=True, text=True
        )
        stdout = result.stdout + result.stderr
        if "nothing to commit" in stdout or "nothing added" in stdout:
            print("  git: 无新变更，跳过推送")
            return

        # 3. git push
        _run(["git", "push", "origin", "main"])
        print(f"  已推送到 GitHub，Pages 将在约 1 分钟内更新")

    except Exception as e:
        print(f"  git push 失败（不影响本地报表）: {e}")


def _run(cmd: list):
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
