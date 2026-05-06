# **0013: Early Termination Resolver (Rage Quits vs. Safe Abandons)**

**Context:** Players frequently leave matches before the MatchEnded event fires—either to quickly queue the next game after a goal hits the ground at 0:00, after an opponent forfeits, or in frustration (a rage quit). The API natively fires a MatchDestroyed event in all these cases, but does not provide an explicit "Forfeit" or "Abandon" event, creating ambiguity around the Win/Loss outcome of the match.  
**Decision:** The Engine will implement an Early Termination Resolver when MatchDestroyed is caught.

1. **Check for Resolution:** It evaluates the bHasWinner flag in the Final Snapshot. If true (meaning the game ended via time, OT, or opponent forfeit right before the player left), the Win/Loss is recorded normally based on the Winner string.  
2. **Check for Abandonment:** If bHasWinner is false, the game was unresolved. The Engine diffs the user's teammates in the Final Snapshot against the MatchInitialized snapshot.  
   * If a teammate is missing, it is a "Safe Abandon": mechanical stats are saved, but the Match is nullified (no Win/Loss recorded).  
   * If all teammates are present, the user is the deserter. It is a "Rage Quit": mechanical stats are saved, and the Match is forcibly recorded as a Loss.

**Consequences:** Match outcomes will perfectly mirror Rocket League's internal penalty logic without requiring invasive memory reading. However, this relies heavily on the assumption that a disconnected player is immediately removed from the UpdateState.Players array by the Rocket League client.