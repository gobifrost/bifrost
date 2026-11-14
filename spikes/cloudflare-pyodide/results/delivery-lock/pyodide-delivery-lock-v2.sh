set -eu
ROOT=/opt/bifrost-tests/pyodide-ddf316c1
PACKET=$ROOT/inbox/delivery-lock-v2
RUN=$ROOT/runs/delivery-lock-v2
test "$(hostname)" = bifrost-pyodide-test-01
mkdir "$PACKET" "$RUN"
tar -xf "$ROOT/inbox/delivery-lock-v2.tar" -C "$PACKET"
IMAGE=bifrost-test-api-dev:bifrost-test-7ddfac36
test "$(docker image inspect "$IMAGE" --format '{{.Id}}')" = sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b
timeout 40 docker run --rm --name pyodide-delivery-lock-v2 --network none --cpus .5 --memory 1g --pids-limit 64 -v "$PACKET:/qualification:ro" -v "$ROOT/upstream-025f6c0/api:/app:ro" -w /qualification -e PYTHONPATH=/app:/qualification -e BIFROST_ENVIRONMENT=testing -e BIFROST_SECRET_KEY=synthetic-unit-fixture-not-an-authenticator "$IMAGE" python -m unittest -v test_attempt_delivery > "$RUN/tests.txt" 2>&1
timeout 40 docker run --rm --name pyodide-delivery-lock-quality-v2 --network none --cpus .5 --memory 1g --pids-limit 64 -v "$PACKET:/qualification:ro" -v "$ROOT/upstream-025f6c0/api:/app:ro" -w /qualification -e NODE_OPTIONS=--max-old-space-size=768 "$IMAGE" sh -c 'ruff check --no-cache attempt_journal.py test_attempt_delivery.py && pyright --project pyright-guest-fixtures.json attempt_journal.py test_attempt_delivery.py' > "$RUN/quality.txt" 2>&1
sha256sum "$PACKET"/*.py > "$RUN/source.sha256"
cat "$RUN/tests.txt" "$RUN/quality.txt" "$RUN/source.sha256"