# Upstream merge 798dda6

Merged upstream main 798dda6dc7d259b1b867b86ff8c4c4f4b7d35bc2. The only textual conflict was .dockerignore; retained both the companion Pyodide exclusions and upstream browser credential/result exclusions.

Qualification: CT128 (bifrost-pyodide-test-01), owned scratch /opt/bifrost-tests/pyodide-ddf316c1/runs/dockerignore-798dda6-v1. Ran python3 /tmp/pyodide-798dda6.py, exit 0. The fixture used docker build --network none with FROM scratch and COPY, then docker create/export without starting the container. Ordinary api/src/sentinel.txt was included; spikes/cloudflare-pyodide, .cloudflare, client/e2e/.auth and client/playwright-results sentinels were excluded. Owned image/container removed. No cloud invocation or laptop Docker.

The initial SSH command had a quoting error before remote test execution; corrected by staging the script. Installed Docker lacks buildx, so the existing legacy builder was used. Source/result retained in the guest scratch directory. Full platform suites and refreshed-main runtime qualification were not run for this ignore-file conflict; prior deployed evidence remains pinned to its recorded source.
