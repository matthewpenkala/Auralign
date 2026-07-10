# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

ARG BASE_IMAGE=python:3.11-slim-bookworm@sha256:f5cf0344c9886ff24d34797578d5d7dd6e8911ae0fe5962bb55d0f89603ec361
ARG PYTHON_VERSION=3.11
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.26@sha256:3d868e555f8f1dbc324afa005066cd11e1053fc4743b9808ca8025283e65efa5
ARG ACCELERATOR_GROUP=cpu
ARG CA_CERTIFICATES_VERSION=20230311+deb12u1
ARG FFMPEG_VERSION=7:5.1.9-0+deb12u1

FROM ${UV_IMAGE} AS uv

FROM ${BASE_IMAGE} AS builder
ARG PYTHON_VERSION
ARG ACCELERATOR_GROUP
ARG CA_CERTIFICATES_VERSION

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates=${CA_CERTIFICATES_VERSION} \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/

RUN uv python install ${PYTHON_VERSION}

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY LICENSES ./LICENSES
COPY compliance ./compliance
RUN uv sync --locked --extra asr --extra diarization --extra alignment \
      --extra speaker-id --extra api --group ${ACCELERATOR_GROUP} \
      --no-dev --no-install-project \
      --python ${PYTHON_VERSION}
COPY src ./src
RUN uv sync --locked --extra asr --extra diarization --extra alignment \
      --extra speaker-id --extra api --group ${ACCELERATOR_GROUP} \
      --no-dev --no-editable \
      --python ${PYTHON_VERSION}

FROM ${BASE_IMAGE} AS runtime
ARG CA_CERTIFICATES_VERSION
ARG FFMPEG_VERSION

LABEL org.opencontainers.image.title="Auralign / Local Transcript Max" \
      org.opencontainers.image.vendor="Matthew Penkala" \
      org.opencontainers.image.licenses="NOASSERTION" \
      io.auralign.project-license="LicenseRef-PolyForm-Internal-Use-1.0.0" \
      io.auralign.distribution-status="internal-only-not-cleared-for-redistribution"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:${PATH}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates=${CA_CERTIFICATES_VERSION} \
      ffmpeg=${FFMPEG_VERSION} \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 ltm \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin ltm \
    && mkdir -p /app /input /output /models /hf-cache /speaker-profiles \
    && chown -R ltm:ltm /app /input /output /models /hf-cache /speaker-profiles

COPY --from=builder /opt/python /opt/python
COPY --from=builder --chown=ltm:ltm /app/.venv /app/.venv
COPY --chown=ltm:ltm LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/share/doc/auralign/
COPY --chown=ltm:ltm LICENSES /usr/share/doc/auralign/LICENSES
COPY --chown=ltm:ltm compliance /usr/share/doc/auralign/compliance

ENV LTM_MODEL_CACHE=/models \
    HF_HOME=/hf-cache \
    HF_HUB_CACHE=/hf-cache/hub \
    LTM_API_JOB_STORE=/output/api/.job-store \
    LTM_SPEAKER_PROFILE_DIR=/speaker-profiles

WORKDIR /app
VOLUME ["/input", "/output", "/models", "/hf-cache", "/speaker-profiles"]
USER ltm
ENTRYPOINT ["/app/.venv/bin/ltm"]
