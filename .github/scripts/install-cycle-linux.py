"""Hosted Ubuntu fixture account. No shell interpolation, inherited tokens or owner HOME."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time

SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}


def command(*args, check=True):
    return subprocess.run(args, check=check, env=SAFE_ENV, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def save(path, data):
    path.write_text(json.dumps(data))
    path.chmod(0o600)


def load(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("Unsafe fixture state")
    data = json.loads(path.read_text())
    sandbox = Path(data["sandbox"])
    if sandbox.parent != Path(data["job_temp"]) or not sandbox.name.startswith("z152-linux-"):
        raise RuntimeError("Foreign sandbox")
    if not re.fullmatch(r'z152fi[0-9a-f]{12}', data['user']):
        raise RuntimeError("Foreign account")
    return data


def setup(args, state):
    if state.parent.stat().st_uid != 0 or state.parent.stat().st_mode & 0o022:
        raise RuntimeError('Expected protected helper directory')
    job_temp = Path(args.job_temp).resolve()
    if not job_temp.is_dir() or job_temp.stat().st_uid == 0:
        raise RuntimeError('Expected runner-owned job temp')
    if state.exists():
        raise RuntimeError("Fixture state already exists")
    sandbox = Path(tempfile.mkdtemp(prefix="z152-linux-", dir=job_temp))
    sandbox.chmod(0o711)
    profile = sandbox / 'profile'
    user = 'z152fi' + secrets.token_hex(6)
    data = dict(sandbox=str(sandbox), profile=str(profile), user=user, job_temp=str(job_temp),
                destination=str(Path(args.source).resolve() / 'output/proof'))
    save(state, data)  # Cleanup can recover even when setup fails halfway.
    command('useradd', '--no-create-home', '--home-dir', str(profile), '--shell', '/bin/bash',
            '--password', '!', '--comment', 'z152 disposable fixture', user)
    account = pwd.getpwnam(user)
    data['uid'], data['gid'] = account.pw_uid, account.pw_gid
    save(state, data)
    profile.mkdir(mode=0o700)
    os.chown(profile, account.pw_uid, account.pw_gid)
    (sandbox / 'fixture-owner.json').write_text(json.dumps(data))
    (sandbox / 'fixture-owner.json').chmod(0o644)
    # Deny siblings on each runner-owned ancestor. Traversal to this sandbox
    # must not grant access to the runner's credentials or original checkout.
    data['acl_paths'] = []
    runner_uid = job_temp.stat().st_uid
    child = sandbox
    for parent in [job_temp, *job_temp.parents]:
        if parent == Path('/') or parent.stat().st_uid != runner_uid:
            break
        for sibling in parent.iterdir():
            if sibling != child and not sibling.is_symlink():
                data['acl_paths'].append(str(sibling))
                save(state, data)
                command('setfacl', '-m', f'u:{user}:---', str(sibling))
        data['acl_paths'].append(str(parent))
        save(state, data)
        command('setfacl', '-m', f'u:{user}:--x', str(parent))
        child = parent
    shutil.copytree(args.source, sandbox / 'cycle', symlinks=True)
    shutil.copy2(args.archive, sandbox / 'source.tgz')
    toolbin = sandbox / 'bin'
    toolbin.mkdir(mode=0o755)
    shutil.copy2(args.node, toolbin / 'node')
    pnpm = Path(args.pnpm).resolve()
    if pnpm.read_bytes()[:4] == b'\x7fELF':
        shutil.copy2(pnpm, toolbin / 'pnpm')
    else:
        shutil.copytree(pnpm.parent.parent, sandbox / 'pnpm', symlinks=True)
        (toolbin / 'pnpm').symlink_to(sandbox / 'pnpm' / pnpm.relative_to(pnpm.parent.parent))
    # setup-python lives in hostedtoolcache, not the runner's private HOME.
    python = Path(args.python).resolve()
    if not python.is_relative_to('/opt/hostedtoolcache/Python'):
        raise RuntimeError('Expected hosted Python runtime')
    (toolbin / 'python3').symlink_to(python)
    (toolbin / 'python').symlink_to(python)
    shutil.copytree(args.python_libs, sandbox / 'python-libs', symlinks=False)
    shutil.copytree(args.browsers, sandbox / 'browsers', symlinks=False)
    for directory in ['cycle', 'python-libs', 'browsers']:
        command('chown', '-R', f'{account.pw_uid}:{account.pw_gid}', str(sandbox / directory))
    (sandbox / 'tmp').mkdir(mode=0o700)
    os.chown(sandbox / 'tmp', account.pw_uid, account.pw_gid)
    command('loginctl', 'enable-linger', user)
    command('systemctl', 'start', f'user@{account.pw_uid}.service')
    for _ in range(100):
        bus = Path(f'/run/user/{account.pw_uid}/bus')
        if bus.exists() and stat.S_ISSOCK(bus.stat().st_mode):
            return
        time.sleep(.1)
    raise RuntimeError('No real user bus')


def run(args, state):
    data = load(state)
    sandbox, uid = Path(data['sandbox']), data['uid']
    account = pwd.getpwnam(data['user'])
    if account.pw_uid != uid or account.pw_dir != data['profile']:
        raise RuntimeError('Fixture account changed')
    env = dict(SAFE_ENV, PATH=f'{sandbox}/bin:/usr/local/bin:/usr/bin:/bin',
               HOME=data['profile'], USER=data['user'], LOGNAME=data['user'],
               RUNNER_TEMP=data['job_temp'], TMPDIR=str(sandbox / 'tmp'),
               XDG_RUNTIME_DIR=f'/run/user/{uid}', DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{uid}/bus',
               DVIZHOK_FIXTURE_PYTHONPATH=str(sandbox / 'python-libs'),
               PLAYWRIGHT_BROWSERS_PATH=str(sandbox / 'browsers'))
    # Python child credentials drop supplementary groups as well as uid/gid.
    result = subprocess.run([str(sandbox / 'bin/node'), 'scripts/reliz/install-cycle.mjs',
                             '--fixture-profile', data['profile'], '--source-archive', str(sandbox / 'source.tgz'),
                             '--source-sha256', args.sha, '--commit', args.commit, '--proof', '../output/proof'],
                            cwd=sandbox / 'cycle/dvizhok', env=env, user=uid, group=data['gid'], extra_groups=[])
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def checked_directory(path, create=False):
    path = Path(path)
    if not path.is_absolute():
        raise RuntimeError('Absolute proof directory required')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not create:
                return None
            current.mkdir(mode=0o755)
            info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise RuntimeError('Proof directory link refused')
    return path


def cleanup(state):
    if not state.exists():
        return
    data = load(state)
    sandbox = Path(data['sandbox'])
    failed = []
    try:
        account = pwd.getpwnam(data['user'])
    except KeyError:
        account = None
    if account and (account.pw_dir != data['profile'] or ('uid' in data and account.pw_uid != data['uid'])):
        raise RuntimeError('Refuse cleanup of foreign account')
    # Stop this user's manager first; it releases every fixture process/file.
    if account:
        for argv in [('systemctl', 'stop', f'user@{account.pw_uid}.service'),
                     ('loginctl', 'disable-linger', data['user']),
                     ('loginctl', 'terminate-user', data['user'])]:
            result = command(*argv, check=False)
            if result.returncode and argv[1] != 'terminate-user':
                failed.append(argv[1])
    try:
        destination = checked_directory(data['destination'], create=True)
        source = checked_directory(sandbox / 'cycle/output/proof')
        if source:
            for name in ['install-cycle-linux.json', 'install-cycle-linux-panel.png', 'install-cycle-linux-conversation.png']:
                file = source / name
                if not file.exists():
                    continue
                info = file.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != data['uid']:
                    raise RuntimeError('Foreign proof file refused')
                target = destination / name
                if target.exists() or target.is_symlink():
                    raise RuntimeError('Existing proof destination refused')
                shutil.copy2(file, target, follow_symlinks=False)
    except Exception:
        failed.append('proof copy')
    if account:
        if command('userdel', data['user'], check=False).returncode:
            failed.append('userdel')
        for path in data.get('acl_paths', []):
            if Path(path).exists():
                command('setfacl', '-x', f'u:{account.pw_uid}', path, check=False)
    try:
        shutil.rmtree(sandbox)
    except Exception:
        failed.append('sandbox removal')
    if failed:
        raise RuntimeError('Cleanup failed: ' + ', '.join(failed))
    state.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['setup', 'run', 'cleanup'])
    parser.add_argument('--state', required=True)
    for name in ['job-temp', 'source', 'archive', 'node', 'pnpm', 'python', 'python-libs', 'browsers', 'sha', 'commit']:
        parser.add_argument('--' + name)
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('Only root on hosted Linux may manage fixture accounts')
    state = Path(args.state).absolute()
    checked_directory(state.parent)
    if state.is_symlink():
        raise RuntimeError('State link refused')
    if args.action == 'setup':
        setup(args, state)
    elif args.action == 'run':
        return run(args, state)
    else:
        cleanup(state)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        # No inherited environment, subprocess command line, or exception data.
        print('Linux fixture failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
