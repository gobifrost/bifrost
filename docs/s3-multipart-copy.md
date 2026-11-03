# Conditional streaming multipart copy

`BIFROST_S3_MULTIPART_COPY_MODE=stream` opts large server-side file copies and
moves into conditional ranged reads followed by multipart part uploads.
The default `server` retains the existing `UploadPartCopy` behavior.
Single-request copies below the copy threshold are unchanged.

Compose installations can set this variable in their deployment `.env` and
recreate the API and worker containers. The isolated debug stack also passes
the setting to its storage consumers. Leaving it unset retains `server` mode.

This option is for a provider that supports conditional ranged `GetObject` and
conditional multipart completion, but does not guarantee conditional
`UploadPartCopy`. For example, Cloudflare's
[R2 compatibility table](https://developers.cloudflare.com/r2/api/s3/api/)
documents `GetObject` If-Match/Range support while marking the source-condition
headers of `UploadPartCopy` unsupported. Bounded R2 probes have accepted those
source conditions; that observation does not establish a provider support
guarantee. This option avoids relying on the undocumented part-copy behavior,
without claiming that every current R2 implementation ignores the condition.

Each range is requested with the original source ETag. Before uploading it, the
client checks the returned ETag, HTTP 206, exact Content-Range, Content-Length
and actual byte count. A changed source, ignored range, truncated body or
cancelled read aborts the private multipart upload. Source metadata and content
headers are preserved. Completion still sends `IfNoneMatch="*"`; an existing
destination is never intentionally overwritten by an unconditional fallback.
The provider must evaluate this completion condition atomically. Streaming is
not a workaround for a provider that cannot do conditional multipart completion.

The streaming default part size is 8 MiB, increased as needed to remain within
10,000 parts. Memory usage scales with part size, not whole-object size; for
very large objects, parts can be much larger than 8 MiB. Payloads transit the
application, so size worker concurrency, memory, request duration and outbound
network costs accordingly. Server-side copying remains preferable where its
source-condition contract is supported.

Qualification must include a real provider's ranged-read headers and checksums,
a same-size source change between parts, destination conflicts and competing
conditional completions. Test fixtures may lower the copy threshold to use
small objects, but those tests do not establish physical multi-gigabyte sizing
or production transfer performance. The setting alone is not provider acceptance.

The change does not alter public PUT IfMatch behavior or enable S3 bucket
versioning. Neither is needed to select this copy strategy.
