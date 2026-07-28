# Agent knowledge retrieval target

## Decision

Bifrost agent knowledge retrieval uses a bounded hybrid-retrieval pipeline:

1. The agent rewrites a user's wording into one or more concise, product-aware
   searches when the user's vocabulary differs from the indexed product.
2. Each agent search retrieves 20 vector candidates and 20 PostgreSQL full-text
   candidates.
3. Reciprocal-rank fusion (RRF, `k=60`, equal lexical/vector weights) combines
   the two rankings using stable chunk IDs.
4. Results are deduplicated by logical document and the best five chunks are
   returned.
5. Across a turn, an evidence ledger prevents the same physical chunk from
   being returned twice and limits the actual serialized evidence to 40,000
   characters. Agent-facing metadata is capped at 2,000 characters per result
   and excludes transport-only image UUID arrays.
6. Eight distinct searches is an emergency loop ceiling. Exact repeat queries
   are rejected without another embedding or database search. The knowledge
   tool is removed when either the query or evidence envelope is exhausted.

The evidence envelope is the primary token control. The query ceiling is not a
retrieval-quality tuning knob and should not be lowered to manage spend.

## Comparison with established implementations

This target follows patterns in products that use retrieval as a feature:

| Product | Candidate retrieval | Fusion/reranking | Deduplication and bounds | Bifrost alignment |
| --- | --- | --- | --- | --- |
| Open WebUI | Vector retrieval plus BM25 over text enriched with filename, title, headings, and source | Weighted ensemble with reciprocal-rank fusion, followed by an optional reranker | Stable content hashes deduplicate chunks; final result count is bounded | Vector plus weighted PostgreSQL FTS, RRF, stable chunk IDs, logical-document dedup, final top five |
| Dify | Semantic and full-text candidates in hybrid mode | Configurable weighted keyword/vector scoring or model reranking | Results are deduplicated by document ID, thresholded, and limited to `top_n` | Deterministic equal-weight RRF initially; the fusion constants are explicit tuning points if a labeled evaluation later supports changing them |

Primary implementation references:

- [Open WebUI retrieval implementation](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/retrieval/utils.py)
- [Dify dataset retrieval](https://github.com/langgenius/dify/blob/main/api/core/rag/retrieval/dataset_retrieval.py)
- [Dify weighted reranking](https://github.com/langgenius/dify/blob/main/api/core/rag/rerank/weight_rerank.py)

Bifrost deliberately uses PostgreSQL full-text search instead of adding a
second retrieval service. The design pattern is the same; the lexical engine is
an implementation choice. A paid model reranker is deferred until a labeled
evaluation demonstrates enough quality improvement to justify its latency and
cost.

## Indexing contract

- Long source documents remain physically chunked before embedding.
- Full-text indexing weights the document key and title as `A`, parent/source
  navigation fields as `B`, and chunk body as `C`.
- Arbitrary metadata JSON is not indexed. Image IDs and ingestion provenance
  create lexical noise and should not influence relevance.
- SDK callers that provide only an embedding retain vector-only behavior.
  Callers that provide the original query text receive hybrid retrieval.

## Acceptance gates

### Automated

- A lexical match outside the first 20 vector candidates must be recoverable
  and rank first after fusion.
- No agent search may return more than five logical documents.
- A repeated query must not call the embedder or repository again.
- A physical chunk already returned in the turn must not be returned again.
- Serialized evidence must never exceed 40,000 characters, and bulky image
  metadata must not enter the agent context.

### Representative Halo replay

Question:

> In ConnectWise, we had the ability to flag people as the Technical POC,
> Billing POC, etc. How should I do that in Halo?

The debug corpus contains production copies of the relevant **Site Contacts**
and **Site Contact Types** articles. The completed replay met these gates:

| Measure | Production failure | Target implementation |
| --- | ---: | ---: |
| Final model input | 973,160 tokens | 41,006 tokens |
| Retrieved/tool payload | 826,039 characters | 33,944 characters |
| Evidence ledger | Unbounded | 30,469 / 40,000 characters |
| Search behavior | 32 calls, 12 unique queries | 14 attempts, 7 unique queries; repeats returned short skips |
| Answer quality | Excessive repeated retrieval | Correctly identified Site Contact Types, gave the configuration and assignment paths, and cited both relevant guides |
| End-to-end duration | Not retained | 23.0 seconds |

This is a 95.8% reduction in final input tokens while passing the answer-quality
gate. Future changes should be evaluated against both the deterministic
repository test and this representative full-agent replay; token reduction
alone is not acceptance.
