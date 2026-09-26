from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
import tempfile
import tomllib

from package_plugin import ROOT, build_plugin

HOSTS = ('codex', 'cursor', 'grok', 'devin')
START = '# orchestra-tasks:start\n'
END = '# orchestra-tasks:end\n'


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_path(path: Path) -> None:
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError(f'symlink parent: {parent}')
    if not path.is_symlink() and path.exists() and path.is_dir():
        raise ValueError(f'expected a file: {path}')


def read(path: Path) -> bytes | None:
    safe_path(path)
    if path.is_symlink():
        return None
    return path.read_bytes() if path.exists() else None


def replace_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.tasks-write-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def block(data: bytes) -> bytes | None:
    text = data.decode()
    if text.count(START) != text.count(END) or text.count(START) > 1:
        raise ValueError('ambiguous Orchestra Tasks config markers')
    if START not in text:
        return None
    a = text.index(START)
    b = text.index(END)
    if b < a:
        raise ValueError('reversed Orchestra Tasks config markers')
    return text[a:b + len(END)].encode()


def hook_config(data: bytes) -> dict:
    parsed = json.loads(data or b'{}')
    if not isinstance(parsed, dict):
        raise ValueError('Devin configuration must be an object')
    hooks = parsed.get('hooks', {})
    if not isinstance(hooks, dict) or not isinstance(hooks.get('SessionStart', []), list):
        raise ValueError('Devin SessionStart hooks must be an array')
    for item in hooks.get('SessionStart', []):
        if not isinstance(item, dict) or not isinstance(item.get('hooks'), list):
            raise ValueError('Devin hook matcher must contain an array')
        if any(not isinstance(hook, dict) for hook in item['hooks']):
            raise ValueError('Devin hooks must be objects')
    return parsed


def selected_hook(data: bytes, command: str) -> list[dict]:
    parsed = hook_config(data)
    return [item for item in parsed.get('hooks', {}).get('SessionStart', [])
            if any(hook.get('command') == command for hook in item['hooks'])]


def render_hook(data: bytes, command: str, wanted: bool) -> bytes:
    parsed = hook_config(data)
    hooks = parsed.setdefault('hooks', {})
    entries = [item for item in hooks.get('SessionStart', [])
               if not any(hook.get('command') == command for hook in item.get('hooks', []))]
    if wanted:
        entries.append({'matcher': '', 'hooks': [{'type': 'command', 'command': command, 'timeout': 10}]})
    if entries:
        hooks['SessionStart'] = entries
    else:
        hooks.pop('SessionStart', None)
    if not hooks:
        parsed.pop('hooks', None)
    return (json.dumps(parsed, indent=2) + '\n').encode()


def roots(home: Path, runtime: Path, codex_home: Path) -> None:
    home = home.absolute()
    forbidden = [home / '.orchestra', Path(os.environ.get('ORCHESTRA_HOME', home / '.orchestra')),
                 home / '.codex', home / '.cursor', home / '.grok', home / '.config',
                 home / '.agents', home / '.local', codex_home, ROOT, home / 'worktrees']
    for other in forbidden:
        other = other.resolve()
        if runtime == other or runtime.is_relative_to(other) or other.is_relative_to(runtime):
            raise ValueError(f'Tasks runtime overlaps a protected root: {other}')
    selected_worktrees = os.environ.get('ORCHESTRA_WORKTREE_ROOT')
    if selected_worktrees:
        other = Path(selected_worktrees).expanduser().resolve()
        if runtime == other or runtime.is_relative_to(other) or other.is_relative_to(runtime):
            raise ValueError('Tasks runtime overlaps selected worktrees')
    configured = Path(os.environ.get('ORCHESTRA_HOME', home / '.orchestra')) / 'worktree-root'
    if configured.is_file():
        other = Path(configured.read_text().strip()).expanduser().resolve()
        if runtime == other or runtime.is_relative_to(other) or other.is_relative_to(runtime):
            raise ValueError('Tasks runtime overlaps configured worktrees')
    for parent in (runtime, *runtime.parents):
        if parent.is_symlink():
            raise ValueError(f'symlink runtime root: {parent}')


def desired(home: Path, runtime: Path, hosts: set[str], temporary: Path, codex_home: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    if not hosts:
        return result
    bundle = build_plugin(ROOT, temporary / 'portable/orchestra-tasks', 'portable')
    for path in bundle.rglob('*'):
        if path.is_file():
            target = runtime / path.relative_to(bundle)
            result[str(target)] = {'kind': 'file', 'content': path.read_bytes()}
    for host in hosts:
        if host in ('codex', 'grok'):
            target = home / '.agents/skills/orchestra-task'
            result[str(target)] = {'kind': 'link', 'target': str(runtime / 'skills/orchestra-task')}
        if host == 'cursor':
            plugin = build_plugin(ROOT, temporary / 'cursor/orchestra-tasks', 'cursor')
            for path in plugin.rglob('*'):
                if path.is_file():
                    target = home / '.cursor/plugins/local/orchestra-tasks' / path.relative_to(plugin)
                    result[str(target)] = {'kind': 'file', 'content': path.read_bytes()}
            target = home / '.cursor/plugins/local/orchestra-tasks/mcp.json'
            result[str(target)] = {'kind': 'file', 'content': (json.dumps({'mcpServers': {'orchestra_tasks': {'command': sys.executable, 'args': [str(runtime / 'scripts/task_mcp.py')]}}}, indent=2) + '\n').encode()}
        if host == 'devin':
            target = home / '.config/devin/skills/orchestra-task'
            result[str(target)] = {'kind': 'link', 'target': str(runtime / 'skills/orchestra-task')}
            target = runtime / 'scripts/devin_task_identity.py'
            result[str(target)] = {'kind': 'file', 'content': (ROOT / 'hosts/devin/task_identity.py').read_bytes()}
            command = f'python3 "{target}"'
            result[str(home / '.config/devin/config.json')] = {'kind': 'hook', 'command': command}
        if host == 'codex':
            target = codex_home / 'config.toml'
            content = (START + '[mcp_servers.orchestra_tasks]\ncommand = ' + json.dumps(sys.executable) + '\nargs = [' + json.dumps(str(runtime / 'scripts/task_mcp.py')) + ']\nrequired = false\ntool_timeout_sec = 3600\ndefault_tools_approval_mode = "writes"\n' + END).encode()
            result[str(target)] = {'kind': 'block', 'content': content}
    return result


def observed(path: Path, entry: dict) -> str | None:
    safe_path(path)
    kind = entry['kind']
    if kind == 'link':
        if path.is_symlink():
            return digest(os.readlink(path).encode())
        if path.exists():
            raise ValueError(f'unowned non-link destination: {path}')
        return None
    if path.is_symlink():
        raise ValueError(f'unexpected symlink: {path}')
    data = read(path)
    if kind == 'file':
        return digest(data) if data is not None else None
    if kind == 'block':
        part = block(data or b'')
        return digest(part) if part is not None else None
    if kind == 'hook':
        entries = selected_hook(data or b'{}', entry['command'])
        return digest(json.dumps(entries, sort_keys=True).encode()) if entries else None
    raise ValueError('unknown manifest entry kind')


def allowed(path: Path, entry: dict, home: Path, runtime: Path, codex_home: Path) -> bool:
    if not path.is_absolute() or path != Path(os.path.abspath(path)) or not isinstance(entry, dict):
        return False
    if entry.get('kind') == 'file':
        return path.is_relative_to(runtime) and path != runtime / 'install-manifest.json' and not path.is_relative_to(runtime / 'backup') or path.is_relative_to(home / '.cursor/plugins/local/orchestra-tasks')
    if entry['kind'] == 'link':
        return path in (home / '.agents/skills/orchestra-task', home / '.config/devin/skills/orchestra-task') and entry.get('target') == str(runtime / 'skills/orchestra-task')
    if entry['kind'] == 'block':
        return path == codex_home / 'config.toml'
    if entry['kind'] == 'hook':
        return path == home / '.config/devin/config.json' and entry.get('command') == f'python3 "{runtime}/scripts/devin_task_identity.py"'
    return False


def synchronize(home: Path, runtime: Path, host: str, action: str, dry_run: bool, *, codex_home: Path | None = None) -> dict:
    home, runtime = home.absolute(), runtime.absolute()
    codex_home = (codex_home or home / '.codex').absolute()
    roots(home, runtime, codex_home)
    manifest_path = runtime / 'install-manifest.json'
    raw = read(manifest_path)
    manifest = json.loads(raw) if raw else {'hosts': [], 'entries': {}}
    if not isinstance(manifest, dict) or set(manifest) != {'hosts', 'entries'} or not isinstance(manifest['hosts'], list) or any(not isinstance(host, str) or host not in HOSTS for host in manifest['hosts']):
        raise ValueError('invalid Tasks manifest')
    old = manifest['entries']
    if not isinstance(old, dict):
        raise ValueError('invalid Tasks manifest entries')
    for path, entry in old.items():
        if not isinstance(entry, dict) or not isinstance(entry.get('digest'), str) or not allowed(Path(path), entry, home, runtime, codex_home):
            raise ValueError(f'unsafe manifest destination: {path}')
    selected = set(HOSTS) if host == 'all' else {host}
    hosts = set(manifest['hosts']) - selected if action == 'uninstall' else set(manifest['hosts']) | selected
    with tempfile.TemporaryDirectory(prefix='orchestra-tasks-sync-') as directory:
        want = desired(home, runtime, hosts, Path(directory), codex_home)
        operations = []
        for name in sorted(set(old) | set(want)):
            path = Path(name)
            owner = old.get(name)
            entry = want.get(name)
            current = observed(path, owner or entry)
            if owner and current != owner['digest']:
                raise ValueError(f'owned destination drift: {path}')
            if not owner and current is not None:
                raise ValueError(f'unowned destination: {path}')
            if entry and entry['kind'] == 'block' and not owner:
                parsed = tomllib.loads((read(path) or b'').decode())
                if 'orchestra_tasks' in parsed.get('mcp_servers', {}):
                    raise ValueError('retire the combined core Task MCP registration before installing Tasks')
            data = read(path) if not path.is_symlink() else None
            if entry and entry['kind'] == 'file':
                after = entry['content']
            elif (entry or owner)['kind'] == 'block':
                previous = block(data or b'') or b''
                after = (data or b'').replace(previous, b'', 1) if previous else data or b''
                if entry:
                    after = after.rstrip(b'\n') + b'\n' + entry['content']
                tomllib.loads(after.decode())
            elif (entry or owner)['kind'] == 'hook':
                after = render_hook(data or b'{}', (entry or owner)['command'], entry is not None)
            else:
                after = None
            operations.append((path, entry, owner, data, after))
        if dry_run or action == 'status':
            return {'status': 'ok', 'action': action, 'hosts': sorted(manifest['hosts']), 'planned_hosts': sorted(hosts), 'paths': [str(item[0]) for item in operations]}
        backup = runtime / 'backup'
        saved: list[tuple[Path, bytes | None, str | None, int | None]] = []
        try:
            new_entries = {}
            for path, entry, owner, data, after in operations:
                link = os.readlink(path) if path.is_symlink() else None
                mode = path.stat().st_mode & 0o777 if data is not None else None
                saved.append((path, data, link, mode))
                if data is not None:
                    destination = backup / digest(str(path).encode())
                    safe_path(destination)
                    if destination.is_symlink():
                        raise ValueError(f'symlink backup destination: {destination}')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    replace_bytes(destination, data)
                path.parent.mkdir(parents=True, exist_ok=True)
                if (entry or owner)['kind'] == 'link':
                    if path.is_symlink():
                        path.unlink()
                    if entry:
                        path.symlink_to(entry['target'], target_is_directory=True)
                elif after is None:
                    path.unlink(missing_ok=True)
                else:
                    replace_bytes(path, after, mode if mode is not None else 0o600)
                if entry:
                    record = {k: v for k, v in entry.items() if k != 'content'}
                    record['digest'] = observed(path, entry)
                    new_entries[str(path)] = record
            runtime.mkdir(parents=True, exist_ok=True)
            replace_bytes(manifest_path, (json.dumps({'hosts': sorted(hosts), 'entries': new_entries}, indent=2) + '\n').encode())
        except Exception:
            for path, data, link, mode in reversed(saved):
                if path.is_symlink():
                    path.unlink()
                if link is not None:
                    path.symlink_to(link, target_is_directory=True)
                elif data is not None:
                    replace_bytes(path, data, mode)
                else:
                    path.unlink(missing_ok=True)
            if raw is not None:
                replace_bytes(manifest_path, raw)
            else:
                manifest_path.unlink(missing_ok=True)
            raise
        warnings = []
        for path, entry, _, _, _ in operations:
            if entry is None:
                previous = backup / digest(str(path).encode())
                try:
                    safe_path(previous)
                    previous.unlink(missing_ok=True)
                except (OSError, ValueError) as error:
                    warnings.append(f'backup cleanup pending: {previous}: {error}')
        try:
            if backup.is_dir() and not any(backup.iterdir()):
                backup.rmdir()
        except OSError as error:
            warnings.append(f'backup directory cleanup pending: {backup}: {error}')
    return {'status': 'ok', 'action': action, 'hosts': sorted(hosts), 'runtime': str(runtime), 'data_preserved': True, 'warnings': warnings}


def main() -> int:
    parser = argparse.ArgumentParser(description='Manage only Orchestra Tasks resources')
    parser.add_argument('action', choices=('status', 'apply', 'uninstall'))
    parser.add_argument('--host', choices=(*HOSTS, 'all'), default='codex')
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--codex-home', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    runtime = args.runtime or Path(os.environ.get('ORCHESTRA_TASKS_HOME', args.home / '.orchestra-tasks'))
    codex_home = args.codex_home or Path(os.environ.get('CODEX_HOME', args.home / '.codex'))
    try:
        print(json.dumps(synchronize(args.home, runtime, args.host, args.action, args.dry_run, codex_home=codex_home)))
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'blocked', 'reason': str(error)}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
