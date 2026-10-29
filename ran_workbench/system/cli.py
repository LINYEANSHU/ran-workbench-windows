"""Reproducible, non-GUI NR experiment entry, also available with --system-cli."""
import argparse
import json
from pathlib import Path
from .schema import default_experiment,load_experiment,save_experiment
from .simulator import simulate
from ..model import load_project
from ..worker import _atomic_json


def main(argv=None):
    parser=argparse.ArgumentParser(description='3GPP NR 策略实验')
    sub=parser.add_subparsers(dest='action',required=True)
    init=sub.add_parser('init');init.add_argument('path');init.add_argument('--scene')
    run=sub.add_parser('run');run.add_argument('path');run.add_argument('--output',required=True)
    check=sub.add_parser('validate');check.add_argument('path')
    args=parser.parse_args(argv)
    if args.action=='init':
        if Path(args.path).exists():parser.error('destination exists')
        save_experiment(default_experiment(load_project(args.scene) if args.scene else None),args.path)
        print(json.dumps({'ok':True,'path':str(Path(args.path).resolve())}));return 0
    exp=load_experiment(args.path)
    if args.action=='validate':
        print(json.dumps({'ok':True,'name':exp['name']},ensure_ascii=False));return 0
    if Path(args.output).exists():parser.error('output exists; select a new destination')
    result=simulate(exp,progress=lambda event:print(json.dumps(event,ensure_ascii=True),flush=True))
    _atomic_json(Path(args.output),result)
    print(json.dumps({'ok':True,'output':str(Path(args.output).resolve()),'ues':result['ues']},ensure_ascii=True));return 0


if __name__=='__main__':raise SystemExit(main())
