set -eu
ROOT=/opt/bifrost-tests/pyodide-ddf316c1
PACKET=$ROOT/inbox/deadline-recovery-v1
RUN=$ROOT/runs/deadline-recovery-unit-v1
test "$(hostname)" = bifrost-pyodide-test-01
test "$(git -C $ROOT/upstream-025f6c0 rev-parse HEAD)" = 1d33150eb376b71f8a81b427920eda1b0447f829
mkdir "$RUN"
cp -a "$ROOT/inbox/retained-http-v1" "$PACKET"
tar -xf "$ROOT/inbox/deadline-recovery-v1.tar" -C "$PACKET"
IMAGE=bifrost-test-api-dev:bifrost-test-7ddfac36
test "$(docker image inspect "$IMAGE" --format '{{.Id}}')" = sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b
set +e
timeout -k 5 40 docker run --rm --name pyodide-deadline-recovery-unit-v1 --network none --cpus .5 --memory 1g --pids-limit 64 -v "$PACKET:/qualification:ro" -v "$ROOT/upstream-025f6c0/api:/app:ro" -w /qualification -e PYTHONPATH=/app:/qualification -e BIFROST_ENVIRONMENT=testing -e BIFROST_SECRET_KEY=synthetic-unit-fixture-not-an-authenticator "$IMAGE" python -m unittest -v test_private_http_pool.PrivatePoolTests.test_explicit_deadline_recovery_allows_retained_ack_without_rewriting_failed_receipts > "$RUN/unit.txt" 2>&1
CODE=$?
set -e
printf '%s\n' "$CODE" > "$RUN/exit-code.txt"
cat "$RUN/unit.txt"
test "$CODE" = 1