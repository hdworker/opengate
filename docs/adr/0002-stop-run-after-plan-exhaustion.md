# Stop scheduling a Run after the full candidate plan is exhausted

Status: accepted. When the free and paid Candidate Plan has no remaining usable candidate, the Run stops scheduling not-yet-started Items instead of waiting for them to reach the same inevitable failure. Active Attempts may finish under their existing timeout because active cancellation is outside the current scope; unscheduled Items are reported as `not_started` and may be explicitly submitted by a consumer in a later Run.

**Consequences**: a Run may finish with `not_started` Items and a terminal plan-exhausted outcome; this behavior must be visible in the result and must not be confused with Item-level invalid input or provider rejection.
