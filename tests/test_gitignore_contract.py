# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import subprocess
from pathlib import Path

from local_transcript_max.cli import _CANONICAL_JOB_ARTIFACTS, _STRICT_VALIDATION_ARTIFACTS

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_private_job_artifact_contract_is_git_ignored() -> None:
    artifacts = [Path("arbitrary-job") / name for name in _CANONICAL_JOB_ARTIFACTS]
    for name, kind in _STRICT_VALIDATION_ARTIFACTS:
        path = Path("arbitrary-job") / name
        artifacts.append(path / "candidate_001.json" if kind == "directory" else path)

    for artifact in artifacts:
        completed = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", "--", str(artifact)],
            cwd=REPO_ROOT,
            check=False,
        )
        assert completed.returncode == 0, f"private generated artifact is not ignored: {artifact}"
