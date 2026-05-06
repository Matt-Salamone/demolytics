# **0012: Lifecycle Resolution and Final Snapshot Merge**

**Context:** The asynchronous nature of the Stats API creates race conditions at the end of a match. If an overtime goal is scored, the Engine pauses the mechanical aggregator (GoalScored event), potentially dropping the subsequent UpdateState tick containing the final score and winner. Additionally, matches that fail to start due to disconnected players fire match lifecycle events but never fire a RoundStarted event, resulting in false "losses" being recorded in V1.  
**Decision:** We will implement a two-part end-of-match resolution process.

1. **Match Validity Gate:** When a match terminates (MatchEnded or MatchDestroyed), the Engine verifies if the Active Gameplay Phase accumulated any time. If zero time accumulated, the match is discarded entirely.  
2. **Final Snapshot Merge:** For valid matches, the Engine does not attempt to manually aggregate the final score. Instead, upon receiving MatchEnded, it grabs the single most recent UpdateState tick from the WebSocket buffer. It reads the official API scoreboard metrics (Winner, Goals, Saves) from this tick, merges them with the paused In-Memory mechanical aggregations, and executes a single SQLite insertion.

**Consequences:** This structurally eliminates the false-loss bug from V1 and perfectly resolves the overtime race condition without requiring complex secondary state machines or delayed write buffers.