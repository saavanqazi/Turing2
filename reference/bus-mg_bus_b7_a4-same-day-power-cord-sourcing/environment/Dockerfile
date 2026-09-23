# Harbor CLI task image — file-deliverable, needs nothing outside input/.
#
# Carries three things: a shell for the agent, the read-only fixtures, and the
# python deps the vendored verifier engine needs. Installing the grader's deps
# at build time (rather than in tests/test.sh) keeps verification independent of
# network conditions at run time.
# python:3.12-slim-bookworm, pinned to its immutable index digest (linux/amd64 + arm64)
FROM python@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

# E2B runs the image as uid 1000 unless the image says otherwise, and the
# agent's first act is mkdir /logs/agent, which then fails. Declared here so
# both the build steps and the runtime user are root (preflight DOCK-4).
USER root

# curl + ca-certificates: Harbor's installed agents (claude-code, codex, ...)
# bootstrap node via nvm inside this container during agent setup.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# The engine's third-party imports are exactly pydantic, jsonpath_ng and
# tenacity; the pins match infra/task-harness/requirements.txt so a verdict in
# here means what it means in the harness. tenacity arrives via the judge module,
# which is imported at package init even though these tasks never call a judge —
# omitting it fails the whole test collection, not just the rubric path.
RUN pip install --no-cache-dir \
        "pytest==8.4.1" \
        "pytest-json-ctrf==0.3.5" \
        "pydantic==2.12.5" \
        "jsonpath-ng>=1.6,<2" \
        "tenacity>=9.0,<10"

# Document-format support, twice over: the AGENT produces docx/pptx/pdf
# deliverables with these (a task demanding a .docx from a container that cannot
# make one is unfair, not hard), and the vendored engine's lazy extractors parse
# them at verification time. Pins match infra/task-harness/requirements.txt so a
# parse verdict in here means what it means in the harness venv.
#
# The engine's docx/pptx/xlsx readers parse package XML through defusedxml, its
# pdf reader (check_pages / extract_text) needs PyMuPDF, its docx/xlsx/html
# comparators use rapidfuzz, and the html source needs lxml + cssselect. Without
# these a check does not fail — it ERRORS with ModuleNotFoundError, which score.py
# counts as a failed check, so a correct deliverable would score 0.0 (Phase 2,
# 2026-09-03: 19 document checks across the 13 live bundles — docx 7 + pptx 6 on
# defusedxml, pdf 6 on PyMuPDF — were one missing import away from that).
RUN pip install --no-cache-dir \
        "python-docx==1.1.2" \
        "python-pptx==1.0.2" \
        "pypdf==6.14.2" \
        "openpyxl==3.1.5" \
        "reportlab==4.5.1" \
        "defusedxml==0.7.1" \
        "PyMuPDF==1.24.14" \
        "rapidfuzz==3.10.1" \
        "lxml==6.1.1" \
        "cssselect==1.3.0"

WORKDIR /app

# Read-only task inputs. The prompt refers to these as `input/`.
COPY input/ /app/input/
RUN chmod -R a-w,a+rX /app/input
