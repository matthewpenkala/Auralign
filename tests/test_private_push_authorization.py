# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_private_push_authorization.py"
SPEC = importlib.util.spec_from_file_location("check_private_push_authorization", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
authorization = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = authorization
SPEC.loader.exec_module(authorization)


def _state(**overrides):
    values = {
        "repository": "example/current-private-repo",
        "private": True,
        "permission": "WRITE",
        "default_branch": "main",
        "push_url": "git@github.com:example/current-private-repo.git",
        "current_branch": "main",
        "dirty": False,
        "behind": 0,
        "ahead": 1,
    }
    values.update(overrides)
    return authorization.PushState(**values)


@pytest.mark.parametrize("permission", ["WRITE", "MAINTAIN", "ADMIN"])
def test_private_push_authorization_is_dynamic_for_write_collaborators(permission: str) -> None:
    authorization.validate(_state(permission=permission))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"private": False}, "private repository"),
        ({"permission": "READ"}, "write permission"),
        ({"push_url": "git@github.com:other/repository.git"}, "does not match"),
        ({"current_branch": "feature"}, "default branch"),
        ({"dirty": True}, "clean"),
        ({"behind": 1}, "behind"),
    ],
)
def test_private_push_authorization_fails_closed(overrides, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        authorization.validate(_state(**overrides))
