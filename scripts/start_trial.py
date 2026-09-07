"""Start one trial server in Codespaces; keep the forwarded port private."""
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:5057/api/health'


def healthy():
    try:
        with urllib.request.urlopen(URL, timeout=2) as response:
            body = json.load(response)
            return body.get('ok') is True and body.get('app') == 'smartplate'
    except (OSError, ValueError, urllib.error.URLError):
        return False


def main():
    if healthy():
        print('SmartPlate is already running. Open port 5057 in the Ports tab.')
        return
    with (ROOT / 'smartplate-trial.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, 'run.py'], cwd=ROOT,
                                   stdout=log, stderr=log, start_new_session=True)
    for _ in range(60):
        if healthy():
            print('SmartPlate is ready. Open port 5057 in the Ports tab.')
            return
        if process.poll() is not None:
            break
        time.sleep(.5)
    raise SystemExit('SmartPlate did not start. Check smartplate-trial.log.')


if __name__ == '__main__':
    main()
