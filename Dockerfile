# Runtime image for the tauargus CLI + TUI. The release workflow builds it
# with the manylinux wheels produced by the same run as the build context.
# Installing the TUI wheel also pulls pytauargus; the run's native pytauargus
# wheel is installed explicitly so the engine is hermetic (no PyPI fetch).
# textual (a TUI-only dep) is still pulled from PyPI.
FROM python:3.12-slim
ARG TARGETARCH
RUN apt-get update \
    && apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=bind,target=/wheels \
    case "$TARGETARCH" in arm64) A=aarch64;; *) A=x86_64;; esac; \
    pip install --no-cache-dir \
      /wheels/pytauargus-*-cp312-*manylinux*_${A}.whl \
      /wheels/pytauargus_tui-*.whl
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin tauargus \
    && mkdir -p /work \
    && chown tauargus:tauargus /work
WORKDIR /work
USER tauargus
ENTRYPOINT ["tauargus"]
