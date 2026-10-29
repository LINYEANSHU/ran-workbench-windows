"""Verify or restore the exact published NR reference files; never runs at startup."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]/'ran_workbench/system/reference'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    manifest = json.loads((ROOT/'manifest.json').read_text())
    for entry in manifest['files']:
        path = ROOT/entry['path']
        if args.download:
            with urllib.request.urlopen(entry['source'], timeout=40) as response:
                payload = response.read()
            if hashlib.sha256(payload).hexdigest() != entry['sha256']:
                raise ValueError('Downloaded reference checksum mismatch')
            path.write_bytes(payload)
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Checksum mismatch: '+path.name)
        print('verified', path.name, entry['sha256'])


if __name__ == '__main__':
    main()
