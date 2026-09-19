# OpenGate Execution Context

This context defines the language of the OpenGate execution boundary: selecting models from the live OpenCode catalog, executing prompts, and processing batches with diagnosable outcomes.

## Execution

**OpenGate Execution Module**:
The domain boundary responsible for catalog snapshots, candidate planning, model attempts, execution outcomes, and batch progress. It does not own project-specific prompts, source data, or worker logic.

**Run**:
A single execution operation with one configuration and one catalog snapshot. A Run may execute one prompt or a batch of Items.

**Item**:
One input unit within a Run. In a JSONL batch, an Item is identified by its stable input index and has one final outcome plus an ordered history of Attempts.

**Attempt**:
One concrete execution of an Item against one selected model. A retry or model switch creates another Attempt; it does not overwrite the previous diagnostic record.

**Session**:
The OpenCode conversation resource used by an Attempt. Session identity is execution metadata, not the identity of the Item or Run.

## Catalog and model policy

**Catalog Snapshot**:
The immutable view of the live OpenCode model catalog captured for a Run. It records the models, capabilities and timestamp used to build that Run's candidate plan. Catalog data is advisory; the real model invocation remains the final availability check.

**Preset**:
A named execution requirement that determines model eligibility and preference order, such as context size, reasoning, or multimodal input.

**Candidate Plan**:
The ordered list of models that a Run may try for an Item after applying the Preset, current Catalog Snapshot, and model policy.

**Free Model**:
One of the six explicitly known free models accepted by the product policy: Big Pickle (`opencode/big-pickle`), MiMo-V2.5 Free (`opencode/mimo-v2.5-free`), Ling 3.0 Flash Fin Free (`opencode/ling-3.0-flash-fin-free`), Nemotron 3 Ultra Free (`opencode/nemotron-3-ultra-free`), Nemotron 3.5 Lightning Free (`opencode/nemotron-3.5-lightning-free`), or Muse Spark 1.3 Contributor Free (`opencode/muse-spark-1.3-contributor-free`).

**Paid Model**:
A catalog model that is eligible for the Preset but is not in the known Free Model set. Billing classification is a product policy decision, not an inference from a display name.

**Free-first**:
The default model policy: try eligible Free Models in Preset order, then eligible Paid Models after the free candidate pool is exhausted according to the error policy.

**Free-only**:
A model policy that excludes Paid Models from the Candidate Plan.

## Outcomes and diagnostics

**Quota Exhaustion**:
An OpenCode/provider response indicating that the current model or free pool cannot accept more work because of a quota, credit, or account limit. It is distinct from a temporary rate limit.

**Retryable Error**:
An error whose evidence and policy permit another Attempt, such as a bounded gateway outage, timeout, or temporary rate limit.

**Plan Exhaustion**:
The condition in which the Candidate Plan has no remaining model that can be tried under the Run's policy. It is a Run-level stopping signal for unscheduled Items.
