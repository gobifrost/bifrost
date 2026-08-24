FROM docker.io/cloudflare/sandbox:0.12.5-python@sha256:eeef11c90115b41b537d946168629ddd02b35877867e7dbc52325ff933bfa0d2

USER root

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/bifrost-build
COPY builder-runner/requirements.lock ./requirements.lock
RUN pip3 install --no-cache-dir --require-hashes -r requirements.lock

COPY api/src/ ./src/
COPY api/shared/ ./shared/
COPY api/bifrost/ ./bifrost/
COPY api/alembic.ini ./alembic.ini
COPY builder-runner/runner.py ./runner.py

RUN mkdir -p /work \
    && chmod 0555 /opt/bifrost-build/runner.py \
    && ln -s /opt/bifrost-build/runner.py /usr/local/bin/bifrost-sandbox-runner \
    && chown -R 1000:1000 /opt/bifrost-build /work

USER 1000:1000
ENV HOME=/tmp \
    PYTHONPATH=/opt/bifrost-build \
    BIFROST_RUNNER_WORKDIR=/work \
    PYTHONUNBUFFERED=1 \
    npm_config_audit=false \
    npm_config_fund=false \
    npm_config_update_notifier=false

# Keep Cloudflare's Sandbox base-image entrypoint intact. Workflows start the
# fixed one-shot command through the Sandbox SDK after writing the job envelope.
CMD ["sleep", "infinity"]
