# SPDX-License-Identifier: GPL-3.0
"""Public protocol 1: one JSON-RPC 2.0 request/response per CLI invocation."""
import argparse
import json
import sys

from loader import task_catalog as catalog
from loader import task_runtime as runtime

METHODS = ('tasks.list', 'tasks.describe', 'snapshot.get', 'jobs.start',
           'jobs.get', 'jobs.list', 'jobs.active', 'jobs.acknowledge')


def error(request_id, number, code, message):
    return dict(jsonrpc='2.0', id=request_id,
                error=dict(code=number, message=message, data=dict(code=code)))


def required(params, name):
    value = params.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError('Expected non-empty string: ' + name)
    return value


def dispatch(request):
    request_id = request.get('id') if isinstance(request, dict) else None
    if (not isinstance(request, dict) or request.get('jsonrpc') != '2.0'
            or not isinstance(request_id, (str, int)) or isinstance(request_id, bool)
            or not isinstance(request.get('method'), str)):
        return error(None, -32600, 'INVALID_REQUEST', 'Expected one JSON-RPC 2.0 request with an id')
    method = request['method']
    if method not in METHODS:
        return error(request_id, -32601, 'METHOD_NOT_FOUND', 'Unknown method: ' + method)
    params = request.get('params', {})
    if not isinstance(params, dict):
        return error(request_id, -32602, 'INVALID_PARAMS', 'params must be an object')
    try:
        if method == 'tasks.list':
            caps = runtime.capabilities()
            result = {key: caps[key] for key in ('identity', 'tuiVersion', 'writable')}
            result.update(protocol=1, methods=list(METHODS),
                          tasks=[task.describe() for task in catalog.TASKS.values()])
        elif method == 'tasks.describe':
            result = catalog.get(required(params, 'taskId')).describe()
        elif method == 'snapshot.get':
            result = dict(snapshot=runtime.backend.snapshot())
        elif method == 'jobs.start':
            task_id, request_id_value = required(params, 'taskId'), required(params, 'requestId')
            catalog.get(task_id)
            # Existing tasks deliberately have no caller-supplied arguments.
            if params.get('arguments', {}) != {}:
                raise ValueError('This task accepts no arguments')
            result = runtime.start(task_id, request_id_value)
        elif method == 'jobs.get':
            result = runtime.read_status(runtime.JOB_ROOT, required(params, 'jobId'))
        elif method == 'jobs.active':
            result = dict(job=runtime.active_job())
        elif method == 'jobs.list':
            result = dict(jobs=runtime.list_jobs(params.get('limit', 20)))
        else:
            result = runtime.acknowledge(required(params, 'jobId'))
        return dict(jsonrpc='2.0', id=request_id, result=result)
    except catalog.UnsupportedTask as exc:
        return error(request_id, -32004, exc.code, str(exc))
    except runtime.JobError as exc:
        return error(request_id, -32010, exc.code, str(exc))
    except PermissionError as exc:
        return error(request_id, -32003, 'PERMISSION_DENIED', str(exc))
    except FileNotFoundError as exc:
        return error(request_id, -32004, 'NOT_FOUND', str(exc))
    except (ValueError, TypeError) as exc:
        return error(request_id, -32602, 'INVALID_PARAMS', str(exc))
    except Exception as exc:
        return error(request_id, -32603, 'INTERNAL_ERROR', str(exc))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=int, default=1)
    args = parser.parse_args(argv)
    if args.protocol != 1:
        response = error(None, -32001, 'UNSUPPORTED_PROTOCOL', 'Supported protocols: 1')
    else:
        try:
            line = sys.stdin.readline(65537)
            if len(line) > 65536:
                raise ValueError('Request exceeds 64 KiB')
            request = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            response = error(None, -32700, 'PARSE_ERROR', str(exc))
        else:
            response = dispatch(request)
    print(json.dumps(response, ensure_ascii=False))
    return 2 if 'error' in response else 0
