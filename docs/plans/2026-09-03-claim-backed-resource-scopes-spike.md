# Claim-backed resource scopes: spike decision

## Decision

Use two authorization projections because table rows and file paths have different query shapes:

1. **Tables:** store a composite access key on each protected document and compare it with an exact list claim using the existing `in` operator.
2. **Files:** store one path prefix per granted capability and evaluate a file-only `path_within_any` operator from one root policy.

Do not compile path containment into table SQL. In the scale experiment, the prefix expansion took 18,067 ms over 60,000 rows while composite exact membership took 16 ms. A single operator for both surfaces looks simpler but puts the expensive operation on the hottest data path.

This design is attribute-based access control (ABAC): the principal has resolved grant attributes and the resource has either an exact access key or a hierarchical path. The file predicate follows the shape of [OPA's `strings.any_prefix_match`](https://www.openpolicyagent.org/docs/policy-reference/builtins/strings) and XACML 3.0's [`string-starts-with`](https://docs.oasis-open.org/xacml/3.0/xacml-3.0-core-spec-cos01-en.html), with stricter slash-segment boundaries for resource paths.

## Authorization projection

The source-of-truth assignment model may remain normalized for editing. Authorization should read flattened projection rows so a grant resolver performs indexed equality filters and selects one scalar value per row.

### Protected documents

| Field | Example | Purpose |
|---|---|---|
| `site_id` | `site-017` | Business dimension |
| `category_id` | `category-03` | Business dimension |
| `document_access_key` | `site-017:category-03` | Exact authorization key |
| `pdf_path` | `documents/site-017/category-03/pdf/document-104/file.pdf` | View representation |
| `download_path` | `documents/site-017/category-03/download/document-104/source.dwg` | Download representation |

`document_access_key` must be generated from immutable UUIDs, not display names. Stable file paths use the same UUIDs as segments. Renaming a site or category therefore changes only its reference row; it does not rewrite grants, keys, or stored paths. The composite key prevents independent site and category claims from accidentally granting their Cartesian product.

### Table grant projection

`document_access_grants` contains one row per principal and permitted site/category pair:

| Field | Index |
|---|---|
| `user_id` | composite index with `document_access_key` |
| `document_access_key` | composite index with `user_id` |

The custom claim is:

```json
{
  "name": "allowed_document_access_keys",
  "type": "list",
  "query": {
    "table": "document_access_grants",
    "where": {"eq": [{"row": "user_id"}, {"user": "user_id"}]},
    "select": "document_access_key"
  }
}
```

The documents table policy is:

```json
{
  "policies": [
    {
      "name": "read_granted_documents",
      "actions": ["read"],
      "when": {
        "in": [
          {"row": "document_access_key"},
          {"claims": "allowed_document_access_keys"}
        ]
      }
    }
  ]
}
```

The same exact predicate should cover query, get, update, and delete actions as appropriate. Create should validate the submitted access key against the claim before insertion.

### Entitlement-filtered navigation

Keep `site_id` and `category_id` on every `document_access_grants` row alongside the composite key. Component claims may safely filter reference tables for navigation:

```json
{"in": [{"row": "site_id"}, {"claims": "allowed_site_ids"}]}
```

Buildings and floors that inherit site access should also carry `site_id`, making their policies the same indexed membership check without a runtime join. The UI queries its own grant rows once, groups the original site/category pairs, and batch-resolves names for only those UUIDs. Component claims control label visibility; they never replace the composite document policy.

### File grant projection

`file_access_grants` contains one row per principal and permitted path scope:

| Field | Example |
|---|---|
| `user_id` | principal identifier |
| `path_prefix` | `documents/site-017/category-03/pdf` |

A view-only assignment emits the `pdf` prefix. An assignment with source-download permission emits both `pdf` and `download` prefixes. This deliberately duplicates a small amount of derived data so authorization does not need to join dimensions or flatten JSON arrays on every request.

The custom claim is:

```json
{
  "name": "allowed_resource_paths",
  "type": "list",
  "query": {
    "table": "file_access_grants",
    "where": {"eq": [{"row": "user_id"}, {"user": "user_id"}]},
    "select": "path_prefix"
  }
}
```

One file policy at the location root replaces a generated policy row for every possible scope:

```json
{
  "policies": [
    {
      "name": "read_granted_resources",
      "actions": ["read"],
      "when": {
        "path_within_any": [
          {"file": "path"},
          {"claims": "allowed_resource_paths"}
        ]
      }
    }
  ]
}
```

`path_within_any` matches an exact path or slash-delimited descendant. It does not use raw `startswith`: a grant for `site-017` cannot match `site-017-annex`. Missing claims, non-list claims, non-string values, and empty/root prefixes fail closed.

## Performance evidence

The opt-in benchmark seeds 150 sites, 8 categories, 60,000 document rows, 120,000 file metadata rows, 616 permission rows across sparse/medium/broad principals, and 2,400 legacy file-policy prefixes.

Measured in the Dockerized test environment on 2026-09-03:

| Measurement | Result |
|---|---:|
| Seed time | 11.453 s |
| One root file policy, median | 2.397 ms |
| One root file policy, p95 | 3.549 ms |
| 2,400 literal-prefix policies, median | 51.827 ms |
| 2,400 literal-prefix policies, p95 | 232.105 ms |
| In-memory predicate, 16 scopes | 53,216 evaluations/s |
| In-memory predicate, 120 scopes | 27,394 evaluations/s |
| In-memory predicate, 480 scopes | 13,862 evaluations/s |
| Composite table `IN`, 320 keys / 60,000 rows | 16.302 ms, 16,000 matches |

The final table query used a PostgreSQL bitmap heap scan, planned in 0.132 ms and hit 1,513 shared blocks. These timings are diagnostic evidence, not CI thresholds.

An earlier experimental implementation compiled 480 path prefixes into an OR of segment-aware SQL `LIKE` predicates. It returned the same 16,000 rows but took 18,067 ms, compared with 16 ms for composite `IN` in that run. The SQL implementation was removed and the operator is rejected by table-policy validation.

## Verification scope

The spike proves:

- exact and descendant file matches;
- sibling-prefix isolation;
- view-only denial of a download path;
- different users resolving different grant rows;
- a real object written to and read from the object-storage boundary through REST;
- large metadata and permission fixtures without timing assertions in CI.

The large fixture intentionally inserts file metadata rather than 120,000 object bodies. Bulk object upload would primarily measure object-store ingestion and obscure authorization cost. The focused end-to-end test covers actual object I/O separately.

## Remaining boundary

Custom claims currently select one scalar field per source row; they do not flatten a list-valued JSON field. Keep flattened authorization projections for this design. General list flattening may be useful platform work, but it is independent of hierarchical file containment and should be evaluated separately.
