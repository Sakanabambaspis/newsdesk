# Catalog versioning semantics

Type: grilling
Status: open
Blocked by: 01

## Question

What are the workflow catalog's versioning semantics? Decide:

- `name@version` resolution: what does `latest` mean, how do station bindings pin or float;
- immutability rules: append-only `workflow_versions`; update = new version, never mutate history; who may create versions (actor tags);
- retire flag vs delete (retire-never-delete); what a retired workflow does to stations bound to it;
- diff format between two versions (human- and agent-readable);
- seed v2 sections (`workflows`, later `stations`) with `source_keys`-style identity indirection, format gate, and idempotent import;
- validation-on-save vs validation-on-load obligations (Cordis: witnesses at definition).

Answers feed tickets 06 (implementation), 10 (station bindings), 12 (seed v2).
