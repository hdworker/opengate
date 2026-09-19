# Streaming spike boundary

Token streaming is deliberately not part of OpenGate Execution Service v1.
The v1 transport uses the official async SDK request and then reads the
completed session messages. This gives projects a predictable `ExecutionResult`
without requiring them to consume SSE events.

A future spike must verify, against the running OpenCode Serve and the pinned
SDK version:

- event subscription and event-to-session correlation;
- first-token timestamp and true TTFT measurement;
- cancellation/abort semantics;
- partial text when a stream fails;
- whether a partial failure can be resumed safely without an automatic model
  switch;
- backpressure and async iterator behavior for project workers.

The spike may extend `ExecutionTransport`, but it must not silently change the
v1 retry contract. A partial stream is expected to return partial text plus a
typed error, and must not automatically start another model attempt.
