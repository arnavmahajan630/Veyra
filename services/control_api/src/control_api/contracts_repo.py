"""contracts-repo/'s own git history (IF-CONTRACT-YAML: versions are commits).

dulwich, not the git CLI: the shared Python image has no git binary (decision TC6).
The layout matches `make contracts-repo-init`: one seed commit, lightweight tag `seed`.
"""

from __future__ import annotations

from pathlib import Path

from dulwich import porcelain
from dulwich.objects import Commit
from dulwich.refs import Ref
from dulwich.repo import Repo

SEED_TAG = Ref(b"refs/tags/seed")
SEED_AUTHOR = "VEYRA seed <veyra@localhost>"


def _work_files(path: Path) -> list[Path]:
    return sorted(
        p for p in path.rglob("*") if p.is_file() and ".git" not in p.relative_to(path).parts
    )


def _has_commits(repo: Repo) -> bool:
    # After a bare `git init`, HEAD is a symbolic ref to a branch with no commits yet.
    try:
        repo.head()
    except KeyError:
        return False
    return True


def ensure_repo(path: Path) -> str:
    """Give the registry a `seed` tag, creating the repo and first commit if missing.

    Handles every starting state: no .git, `git init` with no commits, and commits
    without the tag (then the current HEAD becomes the seed).
    """
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        porcelain.init(str(path)).close()
    with Repo(str(path)) as repo:
        tagged = SEED_TAG in repo.refs
        has_commits = _has_commits(repo)
    if not tagged:
        if not has_commits:
            commit_all(path, "seed: library contracts", author=SEED_AUTHOR)
        with Repo(str(path)) as repo:
            repo.refs[SEED_TAG] = repo.head()
    return head_commit(path)


def commit_all(path: Path, message: str, *, author: str) -> str:
    """Stage and commit every change under `path`, matching `git add -A && git commit`.

    Deletions are staged too: the paths handed to `WorkTree.stage()` are the union of
    what's on disk and what the index already tracks, and dulwich's stage() drops index
    entries for paths that no longer exist on disk. If the resulting tree is identical
    to HEAD's tree, nothing is committed and the current HEAD sha is returned instead —
    except on an unborn repo (no HEAD yet), which always commits, so `ensure_repo`'s
    seed path still works even for an empty directory.
    """
    identity = author if "<" in author else f"{author} <{author}>"
    with Repo(str(path)) as repo:
        work_paths = {p.relative_to(path).as_posix() for p in _work_files(path)}
        tracked_paths = {entry.decode("utf-8") for entry in repo.open_index()}
        repo.get_worktree().stage(sorted(work_paths | tracked_paths))
        if _has_commits(repo):
            staged_tree = repo.open_index().commit(repo.object_store)
            head_obj = repo[repo.head()]
            assert isinstance(head_obj, Commit)
            if staged_tree == head_obj.tree:
                return repo.head().decode("ascii")
        sha = porcelain.commit(
            repo,
            message=message.encode("utf-8"),
            author=identity.encode("utf-8"),
            committer=identity.encode("utf-8"),
        )
    return sha.decode("ascii")


def head_commit(path: Path) -> str:
    with Repo(str(path)) as repo:
        return repo.head().decode("ascii")


def seed_commit(path: Path) -> str:
    with Repo(str(path)) as repo:
        return repo.get_peeled(SEED_TAG).decode("ascii")


def reset_to_seed(path: Path) -> str:
    seed = seed_commit(path)
    with Repo(str(path)) as repo:
        porcelain.reset(repo, "hard", seed.encode("ascii"))
        tracked = {entry.decode("utf-8") for entry in repo.open_index()}
    for file in _work_files(path):
        if file.relative_to(path).as_posix() not in tracked:
            file.unlink()
    return seed
