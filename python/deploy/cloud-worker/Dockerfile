# Build from an explicitly supplied immutable Python 3.11 base image.
# The trusted checkout is retained: runtime source_identity still checks Git + uv.lock.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG BASE_IMAGE
ARG STPD_SOURCE_REVISION
ARG UV_VERSION=0.11.25
ARG STPD_IMAGE_PROFILE=worker
RUN printf '%s' "$BASE_IMAGE" | grep -Eq '@sha256:[0-9a-f]{64}$'
RUN test -n "$STPD_SOURCE_REVISION" && \
    printf '%s' "$STPD_SOURCE_REVISION" | grep -Eq '^[0-9a-f]{40}$'
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "uv==$UV_VERSION"
RUN git clone https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project.git /opt/stpd
WORKDIR /opt/stpd/python
# The clone layer can predate a new approved commit when Docker reuses its cache.
RUN git fetch origin "$STPD_SOURCE_REVISION" && \
    git checkout --detach "$STPD_SOURCE_REVISION" && \
    case "$STPD_IMAGE_PROFILE" in \
      hub) uv sync --locked --extra cloud --extra research ;; \
      worker) env -u UV_INDEX_URL uv sync --locked --all-extras ;; \
      *) echo "Unknown image profile" >&2; exit 1 ;; \
    esac
ENV STPD_IMAGE_PROFILE=$STPD_IMAGE_PROFILE
LABEL org.opencontainers.image.source="https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project" \
      org.opencontainers.image.revision=$STPD_SOURCE_REVISION \
      org.spireagent.image-profile=$STPD_IMAGE_PROFILE
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/stpd/python/.venv/bin:$PATH"
# Modal uses its own Python runtime for the small wrapper. Model work executes
# in this locked virtual environment, not in Modal's injected SDK environment.
