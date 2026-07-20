# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

UV := uv

.PHONY: all check

all: check

check:
	$(UV) run --frozen python build.py --check-only
