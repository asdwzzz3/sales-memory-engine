#!/usr/bin/env python3
import subprocess, os, sys
from datetime import datetime

REPO_DIR = os.path.expanduser('~/xiaozhi_github_a')

def run(cmd):
    result = subprocess.run(cmd, shell=True, cwd=REPO_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        print(f'ERROR: {result.stderr}')
        return False
    print(result.stdout.strip())
    return True

if '--pull' in sys.argv:
    print('>>> 拉取...')
    run('git pull origin main')
    print('>>> 拉取完成')
else:
    print('>>> 推送...')
    run('git add -A')
    msg = f'auto: sync at {datetime.now().strftime("%Y-%m-%d %H:%M")}'
    run(f'git commit -m "{msg}" || echo "nothing to commit"')
    run('git push origin main')
    print('>>> 推送完成')
