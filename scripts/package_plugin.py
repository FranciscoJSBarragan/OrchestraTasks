from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ('portable', 'cursor', 'grok', 'devin')
HELPERS = ('task_control.py', 'task_mcp.py', 'coordination.py', 'git_identity.py')


def build_plugin(source: Path, output: Path, target: str) -> Path:
    if target not in TARGETS or output.name != 'orchestra-tasks':
        raise ValueError('choose a supported target and an output named orchestra-tasks')
    if output.exists() or output.is_symlink():
        raise ValueError(f'output already exists: {output}')
    files = [(source / 'scripts' / name, Path('scripts') / name) for name in HELPERS]
    for folder in ('skills', 'control'):
        for path in sorted((source / folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                files.append((path, path.relative_to(source)))
    files.extend((source / name, Path(name)) for name in ('WORKFLOW.md', 'README.md', 'ARCHITECTURE.md', 'PROVENANCE.md', 'LICENSE'))
    if target in ('cursor', 'devin'):
        files.append((source / 'hosts' / target / 'task_identity.py', Path('scripts') / f'{target}_task_identity.py'))
    for original, _ in files:
        if original.is_symlink() or not original.is_file() or not original.resolve().is_relative_to(source.resolve()):
            raise ValueError(f'missing or unsafe package source: {original}')
    metadata = json.loads((source / 'packaging/.codex-plugin/plugin.json').read_text())
    output.mkdir(parents=True)
    try:
        for original, relative in files:
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination)
        def write(relative: str, value: dict) -> None:
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, indent=2) + '\n')
        write('.codex-plugin/plugin.json', metadata)
        common = {key: metadata[key] for key in ('name', 'version', 'description', 'author', 'license', 'keywords')}
        if target == 'portable':
            write('plugin.json', {'$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json', **common})
        elif target == 'cursor':
            write('.cursor-plugin/plugin.json', {**common, 'skills': './skills/'})
            write('hooks/hooks.json', json.loads((source / 'hosts/cursor/hooks.json').read_text()))
        elif target == 'devin':
            write('.devin-plugin/plugin.json', {**common, 'skills': './skills/'})
            write('hooks.json', json.loads((source / 'hosts/devin/hooks.json').read_text()))
        else:
            write('.claude-plugin/plugin.json', common)
    except Exception:
        shutil.rmtree(output)
        raise
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description='Build the optional Orchestra Tasks plugin')
    parser.add_argument('--target', choices=TARGETS, default='portable')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        print(build_plugin(ROOT, args.output.absolute(), args.target))
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
