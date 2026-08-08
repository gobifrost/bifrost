FROM docker.io/cloudflare/sandbox:0.12.5

USER root
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates python3 python3-yaml \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/bifrost-builder-runner
COPY builder-runner/runner.py /opt/bifrost-builder-runner/runner.py

RUN mkdir -p /work \
    && chmod 0555 /opt/bifrost-builder-runner/runner.py \
    && ln -s /opt/bifrost-builder-runner/runner.py /usr/local/bin/bifrost-sandbox-runner \
    && chown -R 1000:1000 /opt/bifrost-builder-runner /work

USER 1000:1000
ENV HOME=/tmp \
    BIFROST_RUNNER_WORKDIR=/work \
    npm_config_audit=false \
    npm_config_fund=false \
    npm_config_update_notifier=false

# Cloudflare Sandbox retains its base-image entrypoint. Workflows invoke the
# fixed `bifrost-sandbox-runner` command through the Sandbox SDK.
