# Free-first candidate planning from one catalog snapshot

Status: accepted. Each Run captures one live OpenCode catalog snapshot, applies the selected Preset, and traverses eligible free candidates before paid candidates. The six known free models are product policy and are resolved to exact provider/model keys from the snapshot; this keeps routing deterministic within a Run while allowing the live catalog to report current availability.

**Consequences**: catalog access is not repeated for every batch Item; a Run uses one immutable in-memory snapshot; a changed catalog is naturally handled by a new Run. Durable checkpoint and resume compatibility are outside v1.
