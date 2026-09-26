from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


@dataclass(frozen=True)
class GitRepositoryIdentity:
    checkout_root: Path
    repository_root: Path
    common_dir: Path
    head: str


def _run(repo: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=repo, check=False, capture_output=True, text=True)


def _git(repo: Path, *args: str, literal_pathspecs: bool=False) -> subprocess.CompletedProcess[str]:
    command = ['git']
    if literal_pathspecs:
        command.append('--literal-pathspecs')
    command.extend(args)
    return _run(repo, command)


def worktree_root(path: Path) -> Path | None:
    resolved = path.resolve()
    result = _git(resolved, 'rev-parse', '--show-toplevel')
    if result.returncode or Path(result.stdout.strip()).resolve() != resolved:
        return None
    return resolved


def common_git_dir(repo: Path) -> Path | None:
    result = _git(repo, 'rev-parse', '--git-common-dir')
    if result.returncode:
        return None
    path = Path(result.stdout.strip())
    return (repo / path).resolve() if not path.is_absolute() else path.resolve()


def head_commit(repo: Path) -> str | None:
    return resolve_commit(repo, 'HEAD')


def git_repository_identity(path: Path) -> GitRepositoryIdentity | None:
    checkout = worktree_root(path)
    if checkout is None:
        return None
    common_dir = common_git_dir(checkout)
    head = head_commit(checkout)
    if common_dir is None or head is None:
        return None
    repository = _primary_worktree(checkout, common_dir)
    return GitRepositoryIdentity(checkout_root=checkout, repository_root=repository, common_dir=common_dir, head=head)


def _primary_worktree(checkout: Path, common_dir: Path) -> Path:
    if common_dir.name == '.git':
        candidate = common_dir.parent.resolve()
        if worktree_root(candidate) == candidate and common_git_dir(candidate) == common_dir:
            return candidate
    listed = _git(checkout, 'worktree', 'list', '--porcelain', '-z')
    if listed.returncode:
        return checkout
    record = listed.stdout.split('\x00\x00', 1)[0]
    fields = record.split('\x00')
    if 'bare' in fields:
        return checkout
    entry = next((field[9:] for field in fields if field.startswith('worktree ')), '')
    if entry:
        candidate = Path(entry).resolve()
        if worktree_root(candidate) == candidate and common_git_dir(candidate) == common_dir:
            return candidate
    return checkout


def resolve_commit(repo: Path, revision: str) -> str | None:
    result = _git(repo, 'rev-parse', '--verify', f'{revision}^{{commit}}')
    sha = result.stdout.strip()
    return sha if not result.returncode and SHA_PATTERN.fullmatch(sha) else None
