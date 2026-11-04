# Remote package inventory startup

The remote pool registers its physical package inventory before it claims work.
The inventory subprocess imported `PackageInstallConsumer` and the execution
process pool before running `pip list`. Neither runtime is needed for inventory.

The candidate uses the existing lightweight requirements helper's identical pip
inventory operation, returns inventory before constructing a mutation consumer,
and imports that consumer when a mutation is requested. It preserves the full
inventory and the package-tool environment filter. It does not cache inventory,
omit package synchronization, or move claiming ahead of readiness.

## Component experiment

Three repetitions per variant used fresh disposable containers with the same
official base image, 0.5 CPU, 768 MiB, 64 PIDs and no network. The candidate used
one read-only source overlay; it is a modified-source experiment. No image was
published or deployed by this experiment. The raw samples and image/source
identities are in `2026-11-03-package-inventory-startup.json`.

The timed region includes a new Python package-helper process, importing its
dependencies, running pip, consuming its output and validating the full parsed
inventory against a separate canonical `pip list` reference. The reference runs
outside the timer. SSH, Docker setup and teardown are outside the component
timer. Image and host page caches are uncontrolled and likely cached.

Control median was 3.561 seconds; candidate median was 1.505 seconds, about 2.06
seconds or 57.7% less for this component. All measured runs and correctness checks
returned the exact same 153-package inventory. This is not a full cold-start
result, a Cloudflare SLO, or a claim about package mutation performance. The
physical performance floor is unknown.

## Reproduction

Build the normal repository image separately for each source revision. In an
isolated environment, invoke the helper with identical limits:

```sh
docker run --rm --network none --cpus 0.5 --memory 768m --pids-limit 64 \
  --user 1000:1000 --entrypoint python IMAGE \
  -m src.worker.remote.package_helper '{"operation":"inventory"}'
```

For component timing, launch that Python helper as a subprocess inside each
fresh container and time through parsing and validating its complete JSON output.
Collect the reference with `pip list --format=json` before starting the timer.
Use the same immutable harness for both variants and retain all repetitions.
Do not use the outer Docker timing as the inner helper timing.

The import-boundary regression fails on unchanged upstream and passes on the
candidate. Focused checks are:

```sh
./test.sh tests/unit/test_remote_package_inventory.py tests/unit/test_package_tool_env.py -v --durations=0
./test.sh quality api
```

The observed focused run used a retained isolated test harness at `3f79ac4f`
with the exact candidate helper and test overlays: the control had one expected
import-boundary failure, and the candidate passed all 15 selected tests. That is
not a complete-suite result for current main. A normal current-source CI and
review gate, published upstream image, and matched whole-startup measurements
remain necessary before claiming an official-image startup improvement.
