# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""JSON artifact writer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from local_transcript_max.utils import write_json


def write_json_artifact(path: Path, data: BaseModel | dict[str, Any] | list[Any]) -> None:
    payload: Any
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
    else:
        payload = data
    write_json(path, payload)
