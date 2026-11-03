set -eu
ROOT=/opt/bifrost-tests/pyodide-ddf316c1
PACKET=$ROOT/inbox/delivery-lock-v1
RUN=$ROOT/runs/delivery-lock-v1
test "$(hostname)" = bifrost-pyodide-test-01
test "$(git -C $ROOT/upstream-025f6c0 rev-parse HEAD)" = 1d33150eb376b71f8a81b427920eda1b0447f829
mkdir "$PACKET" "$RUN"
tar -xf "$ROOT/inbox/delivery-lock-v1.tar" -C "$PACKET"
IMAGE=bifrost-test-api-dev:bifrost-test-7ddfac36
test "$(docker image inspect "$IMAGE" --format '{{.Id}}')" = sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b
mkdir "$RUN/before"
cp "$PACKET/test_attempt_delivery.py" "$RUN/before/"
cp "$PACKET/attempt_journal_before.py" "$RUN/before/attempt_journal.py"
set +e
timeout 40 docker run --rm --name pyodide-delivery-lock-before --network none --cpus .5 --memory 1g --pids-limit 64 -v "$RUN/before:/qualification:ro" -v "$ROOT/upstream-025f6c0/api:/app:ro" -w /qualification -e PYTHONPATH=/app:/qualification -e BIFROST_ENVIRONMENT=testing -e BIFROST_SECRET_KEY=synthetic-unit-fixture-not-an-authenticator "$IMAGE" python -m unittest -v test_attempt_delivery > "$RUN/before.txt" 2>&1
BEFORE=$?
set -e
test "$BEFORE" = 1
timeout 40 docker run --rm --name pyodide-delivery-lock-after --network none --cpus .5 --memory 1g --pids-limit 64 -v "$PACKET:/qualification:ro" -v "$ROOT/upstream-025f6c0/api:/app:ro" -w /qualification -e PYTHONPATH=/app:/qualification -e BIFROST_ENVIRONMENT=testing -e BIFROST_SECRET_KEY=synthetic-unit-fixture-not-an-authenticator "$IMAGE" python -m unittest -v test_attempt_delivery > "$RUN/after.txt" 2>&1
timeout 40 docker run --rm --name pyodide-delivery-lock-quality --network none --cpus .5 --memory 1g --pids-limit 64 -v "$PACKET:/qualification:ro" -v "$ROOT/upstream-025f6c0/api:/app:ro" -w /qualification -e NODE_OPTIONS=--max-old-space-size=768 "$IMAGE" sh -c 'ruff check attempt_journal.py test_attempt_delivery.py && pyright --project pyright-guest-fixtures.json attempt_journal.py test_attempt_delivery.py' > "$RUN/quality.txt" 2>&1
sha256sum "$PACKET"/*.py > "$RUN/source.sha256"
printf 'before_exit=%s\n' "$BEFORE"
cat "$RUN/before.txt" "$RUN/after.txt" "$RUN/quality.txt"