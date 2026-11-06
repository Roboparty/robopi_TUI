#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 wentywenty

"""Deploy motion-team parameters to configured LRO motors on can0-can3."""

import argparse
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import uuid

import yaml

from loader.board_context import board_id
from loader.plugin_helpers import result


PLUGIN = {
    'id': 'RP025',
    'station': ['robopi2'],
    'clients': ['tui'],
    'author': 'wentywenty',
    'name': 'Deploy LRO motor parameters on can0-can3',
    'version': '2.2',
    'lifecycle': 'testing',
    'safety': 'active',
    'category': 'Motor',
    'batch': False,
    'confirm': 'Write motion-team parameters to ALL configured LRO motors on can0-can3? Stop motion control first. This also sets CAN_TIMEOUT=0 (timeout protection disabled) and writes Pn settings. Power-cycle the motors after success.',
    'inputs': [
        {
            'name': 'robot',
            'label': '选择机器人配置',
            'type': 'choice',
            'choices': ['24 自由度 (rp1_24dof)', '28 自由度 (rp1)', '自定义路径'],
            'default': '自定义路径',
            'required': True,
        },
        {
            'name': 'config',
            'label': 'robot.yaml 绝对路径',
            'type': 'text',
            'pattern': '/[^\n]+',
            'required': True,
            'when': {'robot': '自定义路径'},
        },
    ],
    'requires': ['can'],
    'depends_on': ['RP001'],
    'timeout_s': 3600,
    'documentation': {
        'title': 'can0-can3 LRO 电机参数批量写入',
        'criterion': '按实际 robot.yaml 调用运控脚本并行写入 can0-can3 上的 LRO 电机并读回；包含 CAN_TIMEOUT=0 与 Pn 参数，成功后需电机重新上下电。',
    },
}

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'deploy_motorlro_params.py'
JOURNAL_DIR = Path('/var/lib/robopi-tui/reports/motor-params')
INTERFACES = ('can0', 'can1', 'can2', 'can3')
ROBOT_FOLDERS = {'24 自由度 (rp1_24dof)': 'rp1_24dof', '28 自由度 (rp1)': 'rp1'}


def config_roots():
    roots = [Path('/opt/roboparty/share/roboparty_inference/robots')]
    homes = [Path('/root'), *sorted(Path('/home').glob('*'))]
    for base in dict.fromkeys([Path.cwd(), Path('/opt/roboparty'), *homes]):
        roots.extend((base / 'roboparty_deploy/src/inference/robots',
                      base / 'src/inference/robots'))
    return roots


def resolve_config(robot, config=None, roots=None):
    if robot == '自定义路径':
        if config is None:
            raise ValueError('请填写 robot.yaml 绝对路径')
        return Path(config)
    if robot not in ROBOT_FOLDERS:
        raise ValueError(f'Unknown robot selection: {robot}')
    if config is not None:
        raise ValueError('预设机器人选项不能同时指定自定义路径')
    matches = sorted({path.resolve() for root in (config_roots() if roots is None else roots)
                      if (path := root / ROBOT_FOLDERS[robot] / 'robot.yaml').is_file()})
    if not matches:
        raise ValueError(f'未找到 {robot} 的 robot.yaml，请返回选择“自定义路径”')
    if len(matches) != 1:
        raise ValueError('找到多份配置，请选择“自定义路径”指定实际文件：' +
                         ', '.join(str(path) for path in matches))
    return matches[0]


def select_targets(config, deploy):
    motors = config.get('motors')
    if not isinstance(motors, dict):
        raise ValueError('robot.yaml has no motors mapping')
    fields = ('motor_interface', 'motor_num', 'motor_type', 'motor_id', 'motor_model')
    if any(not isinstance(motors.get(key), list) for key in fields):
        raise ValueError('robot.yaml motor arrays are incomplete')
    names, counts = motors['motor_interface'], motors['motor_num']
    if (len(names) != len(counts) or len(names) != len(motors['motor_type'])
            or any(not isinstance(name, str) for name in names)
            or len(set(names)) != len(names)
            or any(type(count) is not int or count < 0 for count in counts)):
        raise ValueError('robot.yaml motor bus arrays are invalid')
    if any(len(motors[key]) != sum(counts) for key in ('motor_id', 'motor_model')):
        raise ValueError('robot.yaml motor counts do not match IDs/models')
    protocols = motors.get('motor_interface_type')
    if protocols is not None and (not isinstance(protocols, list) or len(protocols) != len(names)):
        raise ValueError('robot.yaml motor_interface_type does not match buses')
    if any(type(value) is not int for key in ('motor_id', 'motor_model') for value in motors[key]):
        raise ValueError('Motor IDs and models must be integers')
    targets = [target for target in deploy['build_motor_targets'](config, 'LRO')
               if target.iface in INTERFACES]
    if not targets:
        raise ValueError('No configured LRO motors on can0-can3')
    seen = set()
    for target in targets:
        identity = (target.iface, target.motor_id)
        if identity in seen or not 1 <= target.motor_id <= 0x7FE:
            raise ValueError(f'Invalid or duplicate motor ID: {identity}')
        if target.model not in deploy['MODEL_PARAMS']:
            raise ValueError(f'Unknown model {target.model} for {identity}')
        if target.iface_type not in ('can', 'canfd', 'ethercanfd'):
            raise ValueError(f'Unsupported interface type: {target.iface_type}')
        seen.add(identity)
    return targets


def check_link(interface):
    completed = subprocess.run(['ip', '-j', 'link', 'show', 'dev', interface],
                               capture_output=True, text=True, check=True, timeout=3)
    links = json.loads(completed.stdout)
    if len(links) != 1 or 'UP' not in links[0].get('flags', []):
        raise ValueError(f'{interface} is not UP')


def deployment_lock():
    from loader.task_runtime import control_lock
    return control_lock()


def probe(config=None, script=SCRIPT, journal_dir=JOURNAL_DIR, run=subprocess.run,
          check=check_link, which=shutil.which, is_root=None, robot='自定义路径', roots=None,
          lock=deployment_lock, board=None):
    board = board_id()[0] if board is None else board
    details = {'config': str(config) if config else None, 'power_cycle_required': False}
    if board != 'robopi2':
        return result('skip', 'LRO parameter deployment on can0-can3 is only enabled on RoboPi2',
                      details=details)
    try:
        if not (os.geteuid() == 0 if is_root is None else is_root()):
            raise ValueError('Run sudo robopi-tui to write motor parameters')
        config = resolve_config(robot, config, roots)
        details.update(config=str(config), robot=robot)
        if config is None or not config.is_absolute() or not config.is_file():
            raise ValueError('Select the actual absolute robot.yaml file path')
        for tool in ('cansend', 'candump', 'ip'):
            if not which(tool):
                raise ValueError(f'Missing command: {tool}; install can-utils and iproute2')
        deploy = runpy.run_path(str(script))
        source = config.read_text(encoding='utf-8')
        parsed = yaml.safe_load(source)
        if not isinstance(parsed, dict):
            raise ValueError('robot.yaml must contain a mapping')
        targets = select_targets(parsed, deploy)
        interfaces = [name for name in INTERFACES if any(t.iface == name for t in targets)]
        for interface in interfaces:
            check(interface)
        details['targets'] = [vars(target) for target in targets]
        details['interfaces'] = interfaces
        details['can_timeout_ms'] = 0
        journal_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if journal_dir.is_symlink() or (os.name == 'posix' and journal_dir.stat().st_mode & 0o077):
            raise ValueError(f'Motor log directory is not private: {journal_dir}')
        folder = journal_dir / str(uuid.uuid4())
        folder.mkdir(mode=0o700)
        snapshot = folder / 'robot.yaml'
        snapshot.write_text(source, encoding='utf-8')
        snapshot.chmod(0o600)
        log = folder / 'deploy.log'
        command = [sys.executable, '-u', str(script), '--mode', 'write',
                   '--config', str(snapshot), '--parallel']
        for interface in interfaces:
            command.extend(['--interface', interface])
        details.update(log=str(log), config_snapshot=str(snapshot), command=command)
        # Keep children in the executor's process group so its timeout stops every bus.
        with lock(), log.open('x', encoding='utf-8') as output:
            log.chmod(0o600)
            completed = run(command, stdout=output, stderr=subprocess.STDOUT,
                            text=True, check=False)
        details['exit_code'] = completed.returncode
        details['output_tail'] = log.read_text(encoding='utf-8', errors='replace')[-12000:]
        if completed.returncode:
            return result('fail', '电机参数写入或读回失败，可能已有部分参数写入；请检查日志。',
                          details=details)
        details['power_cycle_required'] = True
        return result('pass', '配置中的 LRO 电机参数写入及读回完成。请重新给电机上下电后再使用。',
                      value=len(targets), unit='motors', details=details)
    except (OSError, ValueError, RuntimeError, yaml.YAMLError, subprocess.SubprocessError) as exc:
        return result('error', f'Motor deployment stopped: {exc}', details=details)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path)
    parser.add_argument('--params')
    parser.add_argument('--params-stdin', action='store_true')
    args = parser.parse_args()
    params = (json.loads(sys.stdin.read() if args.params_stdin else args.params)
              if args.params is not None or args.params_stdin else {})
    config = params.get('config', args.config)
    print(json.dumps(probe(Path(config) if config else None,
                           robot=params.get('robot', '自定义路径')), ensure_ascii=False))
