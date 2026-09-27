"""The contract registry's own git history, via dulwich (no git binary in the image)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from control_api.contracts_repo import (
    commit_all,
    ensure_repo,
    head_commit,
    reset_to_seed,
    seed_commit,
)
from dulwich import porcelain


def make_seed(root: Path) -> Path:
    (root / "t_a").mkdir(parents=True)
    (root / "t_a" / "one.yaml").write_text("contract: one\n", encoding="utf-8")
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    return root


def test_ensure_repo_commits_the_seed_and_tags_it(tmp_path: Path) -> None:
    repo = make_seed(tmp_path / "cr")
    sha = ensure_repo(repo)
    assert len(sha) == 40
    assert head_commit(repo) == sha == seed_commit(repo)
    assert ensure_repo(repo) == sha  # idempotent


def test_ensure_repo_seeds_a_git_init_without_commits(tmp_path: Path) -> None:
    repo = make_seed(tmp_path / "cr")
    porcelain.init(str(repo)).close()
    sha = ensure_repo(repo)
    assert len(sha) == 40 and seed_commit(repo) == sha


def test_ensure_repo_tags_existing_history_without_a_seed_tag(tmp_path: Path) -> None:
    repo = make_seed(tmp_path / "cr")
    porcelain.init(str(repo)).close()
    first = commit_all(repo, "hand-made first commit", author="author@maha")
    assert ensure_repo(repo) == first == seed_commit(repo)


def test_reset_to_seed_restores_files_and_drops_later_work(tmp_path: Path) -> None:
    repo = make_seed(tmp_path / "cr")
    seed = ensure_repo(repo)
    (repo / "t_a" / "one.yaml").write_text("contract: one\nversion: 2\n", encoding="utf-8")
    (repo / "t_a" / "two.yaml").write_text("contract: two\n", encoding="utf-8")
    later = commit_all(repo, "authsrv@2", author="author@maha")
    assert later != seed
    (repo / "stray.txt").write_text("untracked\n", encoding="utf-8")
    (repo / "README.md").unlink()

    assert reset_to_seed(repo) == seed
    assert head_commit(repo) == seed
    assert (repo / "t_a" / "one.yaml").read_text(encoding="utf-8") == "contract: one\n"
    assert (repo / "README.md").read_text(encoding="utf-8") == "seed\n"
    assert not (repo / "t_a" / "two.yaml").exists()
    assert not (repo / "stray.txt").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs the git CLI")
def test_a_repo_made_by_make_contracts_repo_init_is_accepted(tmp_path: Path) -> None:
    repo = make_seed(tmp_path / "cr")

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        )
        return done.stdout.strip()

    git("init", "-q", ".")
    git("add", "-A")
    git("-c", "user.email=veyra@localhost", "-c", "user.name=VEYRA seed", "commit", "-q", "-m", "s")
    git("tag", "seed")
    sha = git("rev-parse", "HEAD")
    assert ensure_repo(repo) == sha
    assert seed_commit(repo) == sha
