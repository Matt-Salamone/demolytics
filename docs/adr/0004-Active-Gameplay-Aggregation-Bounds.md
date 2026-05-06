# **0004: Active Gameplay Aggregation Bounds**

**Context:** The Rocket League Stats API continues to broadcast 120Hz UpdateState ticks during goal replays, pre-kickoff countdowns, and match pauses. If the In-Memory Aggregator processes these ticks, metrics reliant on time or total tick counts (like Average Speed, BPM, or Low Boost %) will be severely skewed by periods where player cars are intentionally stationary.  
**Decision:** The Engine's In-Memory Aggregator will only process data during the "Active Gameplay Phase". Aggregation strictly *resumes* upon receiving the RoundStarted or MatchUnpaused events, and strictly *pauses* upon receiving the GoalScored, MatchPaused, or MatchEnded events.  
**Consequences:** \* **Positive:** Averages and percentages will accurately reflect true mechanical gameplay without being diluted by theatrical match phases.

* **Negative:** If a stat occurs in the millisecond *after* a goal but before the replay begins (e.g., a post-goal demolition), it will be explicitly ignored by our statistical engine. This is an acceptable trade-off for overall data integrity.