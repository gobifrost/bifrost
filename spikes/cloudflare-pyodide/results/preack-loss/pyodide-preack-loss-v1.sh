set -eu
ROOT=/opt/bifrost-tests/pyodide-ddf316c1
PACKET=$ROOT/inbox/preack-loss-v1
RUN=$ROOT/runs/preack-loss-unit-v1
test "$(hostname)" = bifrost-pyodide-test-01
test "$(git -C $ROOT/upstream-025f6c0 rev-parse HEAD)" = 1d33150eb376b71f8a81b427920eda1b0447f829
mkdir "$RUN"
cp -a "$ROOT/inbox/late-adapter-v2" "$PACKET"
tar -xf "$ROOT/inbox/preack-loss-v1.tar" -C "$PACKET"
IMAGE=bifrost-test-api-dev:bifrost-test-7ddfac36
test "$(docker image inspect "$IMAGE" --format '{{.Id}}')" = sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b
set +e
timeout -k 5 40 docker run --rm --name pyodide-preack-loss-unit-v1 --network none --cpus .5 --memory 1g --pids-limit 64 -v "$PACKET:/qualification:ro" -v "$ROOT/upstream-025f6c0/api:/app:ro" -w /qualification -e PYTHONPATH=/app:/qualification -e BIFROST_ENVIRONMENT=testing -e BIFROST_SECRET_KEY=synthetic-unit-fixture-not-an-authenticator "$IMAGE" python -m unittest -v test_private_http_pool.PrivatePoolTests.test_transport_loss_before_ack_retains_native_capacity_and_never_relaunches > "$RUN/unit.txt" 2>&1
CODE=$?
set -e
printf '%s\n' "$CODE" > "$RUN/exit-code.txt"
cat "$RUN/unit.txt"
test "$CODE" = 1