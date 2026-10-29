"""The normal entry must route CLI commands before importing Qt or CUDA."""
import json
from pathlib import Path
import subprocess
import sys


def test_main_routes_cli_without_starting_desktop(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / 'main.py'), '--cli', '--endpoint-file',
                             str(tmp_path / 'missing.json'), 'state'], capture_output=True, text=True,
                            encoding='utf-8', timeout=15)
    assert result.returncode == 1
    response = json.loads(result.stdout)
    assert response['ok'] is False and 'Traceback' not in result.stderr
