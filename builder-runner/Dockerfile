FROM docker.io/cloudflare/sandbox:0.12.5-opencode

USER root
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/bifrost-builder-runner
COPY builder-runner/package.json builder-runner/package-lock.json ./
COPY builder-runner/opencode_turn.mjs builder-runner/opencode_turn.test.mjs builder-runner/opencode_turn.integration.test.mjs ./
COPY builder-runner/runner_service.py ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && BIFROST_OPENCODE_INTEGRATION=1 npm test \
    && rm opencode_turn.test.mjs opencode_turn.integration.test.mjs

COPY builder-runner/runner.py ./

RUN mkdir -p /work \
    && chmod 0555 /opt/bifrost-builder-runner/runner.py /opt/bifrost-builder-runner/opencode_turn.mjs \
    && ln -s /opt/bifrost-builder-runner/runner.py /usr/local/bin/bifrost-sandbox-runner \
    && chown -R 1000:1000 /opt/bifrost-builder-runner /work

USER 1000:1000
ENV HOME=/tmp \
    BIFROST_RUNNER_WORKDIR=/work \
    npm_config_audit=false \
    npm_config_fund=false \
    npm_config_update_notifier=false

CMD ["python3", "/opt/bifrost-builder-runner/runner_service.py", "--port", "8300"]

# Cloudflare Sandbox retains its base-image entrypoint. Workflows invoke the
# fixed `bifrost-sandbox-runner` command through the Sandbox SDK.
