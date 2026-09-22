# Workspace Import Kitchen Sink

A representative Solution package for the unattached **Import into workspace**
flow. Every definition below is ordinary portable content; the package-only
declarations (custom claims, connection schemas, file locations, role bindings)
are intentionally present so the preview proves they produce explicit warnings
instead of disappearing silently.

Typed cross-references tie the package together:

- form `sink_intake` launches workflow `sink_alpha`;
- agent `sink_triage` binds the `sink_lookup` tool workflow;
- the `sink_nightly` schedule and `sink_hook` webhook subscribe those
  workflows.

Seeding the destination with older copies of some definitions yields creates,
unchanged items, entity conflicts, and file conflicts in one preview.

The ZIP used by tests and live review is generated from this exact tree:

```bash
cd examples/workspace-import-kitchen-sink
zip -r ../workspace-import-kitchen-sink.zip . -x '.git/*'
```

For the repository path, commit this tree and import with
`--repo <url> [--ref <ref>] [--path <subfolder>]`.
