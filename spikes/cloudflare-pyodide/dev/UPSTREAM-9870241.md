# Reconciliation against upstream 9870241

The update helper returned repository_not_enrolled. A targeted Git ref read
established main `9870241dcfef89f59ffe2edc9add8f6c7dce46b3`; merge-tree reproduced
two conflicts in engine context construction and lineage-test imports.

Resolution retains upstream agent/origin identity fields and the existing trusted
explicit public URL path. Both test import sets are retained. The metadata
regression now asserts agent identity/origin/original-user fields alongside the
public URL while proving server settings are not loaded for explicit runtime input.

CT128 packet: `inbox/merge-9870241-v1`. Full merged api/src, shared and bifrost
source was mounted read-only over the existing cached native dependency image;
four selected test files cover workflow lineage/setup fencing, stream capture,
optional context and runtime metadata. **24 pass in3.01s**, exit0. The earlier
24-test run before strengthening the metadata assertion passed in3.04s.
Ruff passes the resolved engine and changed/conflicted tests.

```sh
pytest -q -o addopts= -o asyncio_mode=auto -o cache_dir=/tmp/pytest-cache \
  --confcutdir=/qualification /qualification
```

Network none,0.5CPU/1GiB/64PIDs/90-second outer bound; no database, cloud or
laptop Docker run. Source is current merge content, but dependencies are the
previous qualified image. New upstream AI/MCP dependency pins were not installed
or broadly qualified here; normal CI remains required for the merged candidate.
The broad diff whitespace check flags inherited upstream CRLF in documentation
and disposition files; resolved files pass scoped whitespace checks. Those
upstream artifacts are not rewritten as part of this conflict resolution.

Packaged Pyodide engine/SDK and the closed cloud artifact remain pinned to prior
receipts. This merge does not claim new upstream-main runtime parity, deployed
canonical execution, hostile isolation or CPU-stop acceptance. No Containers
owner checkout or production resource was changed.
