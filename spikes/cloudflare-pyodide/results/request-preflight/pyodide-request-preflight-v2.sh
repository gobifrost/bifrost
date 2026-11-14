set -eu
ROOT=/opt/bifrost-tests/pyodide-ddf316c1
PACKET=$ROOT/inbox/request-preflight-v2
RUN=$ROOT/runs/request-preflight-quality-v2
test "$(hostname)" = bifrost-pyodide-test-01
test "$(git -C $ROOT/upstream-025f6c0 rev-parse HEAD)" = 1d33150eb376b71f8a81b427920eda1b0447f829
mkdir "$RUN"
cp -a "$ROOT/inbox/deadline-recovery-v2" "$PACKET"
tar -xf "$ROOT/inbox/request-preflight-v2.tar" -C "$PACKET"
IMAGE=bifrost-test-api-dev:bifrost-test-7ddfac36
test "$(docker image inspect "$IMAGE" --format '{{.Id}}')" = sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b
timeout -k 5 50 docker run --rm --name pyodide-retained-http-unit-v1 --network none --cpus .5 --memory 1g --pids-limit 64 -v "$PACKET:/qualification:ro" -v "$ROOT/upstream-025f6c0/api:/app:ro" -w /qualification -e PYTHONPATH=/app:/qualification -e BIFROST_ENVIRONMENT=testing -e BIFROST_SECRET_KEY=synthetic-unit-fixture-not-an-authenticator "$IMAGE" python -m unittest -v test_retained_http_fault test_private_http_pool test_attempt_delivery test_workflow_request_mux test_http_invocation test_workspace_prepare test_attempt_deadline test_attempt_journal_owners > "$RUN/unit.txt" 2>&1
timeout -k 5 50 docker run --rm --name pyodide-request-preflight-quality-v2 --network none --cpus .5 --memory 1g --pids-limit 64 -v "$PACKET:/qualification:ro" -v "$ROOT/upstream-025f6c0/api:/app:ro" -w /qualification -e NODE_OPTIONS=--max-old-space-size=768 "$IMAGE" sh -c 'ruff check --no-cache private_http_pool.py http_invocation.py test_private_http_pool.py && pyright --project pyright-guest-fixtures.json private_http_pool.py http_invocation.py test_private_http_pool.py' > "$RUN/quality.txt" 2>&1
cat "$RUN/unit.txt" "$RUN/quality.txt"