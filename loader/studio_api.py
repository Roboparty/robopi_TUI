#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
"""Legacy API-v1 adapter. Kept for existing clients; task execution is TUI-owned."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loader import task_runtime as runtime

API_VERSION = 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('capabilities', 'snapshot', 'start', 'status', 'active', 'acknowledge', 'worker'))
    parser.add_argument('value', nargs='?')
    parser.add_argument('--request-id')
    parser.add_argument('--job-root', type=Path, default=runtime.JOB_ROOT)
    args = parser.parse_args(argv)
    try:
        if args.operation != 'worker' and args.job_root != runtime.JOB_ROOT:
            raise ValueError('--job-root is only accepted by the internal worker')
        if args.operation == 'capabilities':
            result = runtime.capabilities()
        elif args.operation == 'snapshot':
            result = dict(apiVersion=1, snapshot=runtime.backend.snapshot())
        elif args.operation == 'start':
            result = runtime.start(args.value, args.request_id)
        elif args.operation == 'status':
            result = runtime.read_status(runtime.JOB_ROOT, args.value)
        elif args.operation == 'active':
            result = dict(apiVersion=1, job=runtime.active_job())
        elif args.operation == 'acknowledge':
            result = runtime.acknowledge(args.value)
        else:
            return runtime.worker(args.value, args.job_root)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps(dict(apiVersion=1, error=str(exc), errorType=type(exc).__name__), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
