"""Cross-platform entry point: uv run --locked python run.py."""
import argparse
import os
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    parser=argparse.ArgumentParser(description='Start Worky in your browser.')
    parser.add_argument('--host',default=os.environ.get('WORKY_HOST','0.0.0.0'))
    parser.add_argument('--port',type=int,default=os.environ.get('PORT','8501'))
    args=parser.parse_args(argv)
    if not 1<=args.port<=65535:
        parser.error('--port must be between 1 and 65535')
    root=Path(__file__).resolve().parent
    command=[sys.executable,'-m','streamlit','run',str(root/'app.py'),
             '--server.address',args.host,'--server.port',str(args.port),
             '--server.headless','true','--browser.gatherUsageStats','false']
    with subprocess.Popen(command,cwd=root) as process:
        try:
            return process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return 130


if __name__=='__main__':
    raise SystemExit(main())
