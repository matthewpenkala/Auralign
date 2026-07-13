#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Authorize a normal push using the current private-repository collaborator state."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass

WRITE_PERMISSIONS = frozenset({"ADMIN", "MAINTAIN", "WRITE"})


@dataclass(frozen=True)
class PushState:
    repository: str
    private: bool
    permission: str
    default_branch: str
    push_url: str
    current_branch: str
    dirty: bool
    behind: int
    ahead: int


def _run(*command: str) -> str:
    return subprocess.run(
        list(command), check=True, capture_output=True, text=True
    ).stdout.strip()


def _remote_repository(push_url: str) -> str | None:
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"(?P<repository>[^/\s]+/[^/\s]+?)(?:\.git)?/?",
        push_url,
    )
    return match.group("repository") if match else None


def validate(state: PushState) -> None:
    """Fail closed unless this is a clean, current, write-authorized private push."""

    if not state.private:
        raise RuntimeError("push authorization requires an existing private repository")
    if state.permission not in WRITE_PERMISSIONS:
        raise RuntimeError("authenticated GitHub collaborator lacks write permission")
    if _remote_repository(state.push_url) != state.repository:
        raise RuntimeError("origin push URL does not match the verified private repository")
    if state.current_branch != state.default_branch:
        raise RuntimeError("push authorization requires the repository default branch")
    if state.dirty:
        raise RuntimeError("push authorization requires a clean tracked and untracked worktree")
    if state.behind:
        raise RuntimeError("local default branch is behind the verified remote; fast-forward first")


def collect(remote: str) -> PushState:
    payload = json.loads(
        _run(
            "gh",
            "repo",
            "view",
            "--json",
            "nameWithOwner,isPrivate,viewerPermission,defaultBranchRef",
        )
    )
    default = payload.get("defaultBranchRef")
    if not isinstance(default, dict) or not isinstance(default.get("name"), str):
        raise RuntimeError("GitHub did not return a default branch")
    default_branch = default["name"]
    divergence = _run(
        "git", "rev-list", "--left-right", "--count", f"{remote}/{default_branch}...HEAD"
    )
    behind_text, ahead_text = divergence.split()
    return PushState(
        repository=str(payload.get("nameWithOwner", "")),
        private=payload.get("isPrivate") is True,
        permission=str(payload.get("viewerPermission", "")).upper(),
        default_branch=default_branch,
        push_url=_run("git", "remote", "get-url", "--push", remote),
        current_branch=_run("git", "branch", "--show-current"),
        dirty=bool(_run("git", "status", "--porcelain=v1", "--untracked-files=all")),
        behind=int(behind_text),
        ahead=int(ahead_text),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args()
    try:
        state = collect(args.remote)
        validate(state)
    except (RuntimeError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
        print(f"private_push_authorized=false reason={exc}")
        return 1
    print(
        "private_push_authorized=true "
        f"repository={state.repository} permission={state.permission} "
        f"branch={state.default_branch} ahead={state.ahead} behind={state.behind}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
