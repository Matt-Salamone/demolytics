# **Product Requirements Document**

## **Problem Statement**

Competitive Rocket League players struggle to contextualize their real-time performance. They need immediate, actionable feedback on their mechanical form and tactical flow without disrupting their gameplay. Current solutions are obtrusive, require playing in sub-optimal "Borderless Windowed" modes (sacrificing input latency), or rely on memory injection, which carries a strict ban risk from Easy Anti-Cheat. Furthermore, tracking deep historical data locally often leads to bloated desktop applications that consume excessive system resources while gaming.

## **Solution**

Demolytics V2 is a strictly local-first, zero-ban-risk statistical engine and unobtrusive coaching tool. It utilizes a Decoupled Architecture: a lightweight, background Node.js Engine that ingests local WebSocket telemetry, and a native Windows Game Bar Overlay that safely renders contextual Insights directly over Exclusive Fullscreen gameplay. Acting as an "Objective Mirror," the system evaluates live, in-memory telemetry against an SQLite database of the user's Historical Baselines, surfacing mathematically proven anomalies during goal replays, and managing replay uploads to ballchasing.com seamlessly in the background.

## **User Stories**

1. As a player, I want the application's overlay to run natively via the Windows Game Bar, so that I can play Rocket League in Exclusive Fullscreen mode without risk of Easy Anti-Cheat bans or frame drops.  
2. As a player, I want the Engine to process telemetry purely in memory during gameplay, so that my hard drive doesn't experience I/O thrashing from 120Hz data writes.  
3. As a player, I want the Podium overlay to instantly appear upon the GoalScored event and disappear on RoundStarted, so that the UI never obstructs my active gameplay.  
4. As a player, I want the Engine to prioritize my Historical Baseline over my Live Team averages for Spectator Metrics, so that I can evaluate my personal mechanical form even if my teammates are playing unusually.  
5. As a player, I want the Engine to suspend my individual mechanical aggregation while I am demolished, so that my Average Speed and Boost metrics aren't artificially tanked by tactical deaths.  
6. As a player, I want the overlay to rotate the types of Insights it shows me (e.g., alternating between mechanics and playmaking), so that I don't experience alert fatigue from the same stat constantly popping up.  
7. As a player, I want my Session Recap to be calculated instantly between matches without querying the database, so that I can quickly review my macro trends while queuing for the next match.  
8. As a player, I want the system to correctly identify when a match is a "Rage Quit" versus a "Safe Abandon," so that my local W/L records perfectly mirror Rocket League's actual penalty logic.  
9. As a player, I want the system to automatically and safely detect when I manually save a .replay file, so that it can be uploaded to ballchasing.com in the background without causing the app to stutter or drop live telemetry ticks.  
10. As a user, I want a separate System Tray Dashboard to view my Encounter History, so that I can look up past records with specific players without cluttering the lightweight in-game overlay.  
11. As a user, I want the overlay to instantly snap to the correct state if I manually open it mid-match, so that I never see a blank or broken UI.  
12. As a user with thousands of saved matches, I want the Engine to query my 50-game baseline instantly, so that I am never penalized for keeping my historical data.  
13. As a user, I want the app to automatically configure the Rocket League Stats API on my behalf, so I do not have to dig into my system files to manually edit .ini files.  
14. As a user, if automatic configuration requires administrator privileges, I want to be prompted to either grant elevated permissions safely or view manual instructions, so that I retain full control over my system's security.  
15. As a user, I want my ballchasing.com API key to be stored securely using Windows' native credential manager, so that my private key is protected from unauthorized access or plaintext exposure.

## **Implementation Decisions**

**Architectural Paradigm: Decoupled UWP Presentation and Local Engine (ADR 0001\)**  
The application is split into a background Node.js service (the "Engine") and a frontend Next.js UWP Game Bar Widget (the "Presentation Layer"). The Engine acts as the sole Source of Truth, communicating with the Presentation Layer via a local WebSocket.  
**Deep Module 1: The Telemetry Ingestion Pipeline**  
This module encapsulates the immense complexity of raw API Ticks into a clean, In-Memory Aggregator (ADR 0002).

* **Interface:** processTick(tickPayload) \-\> updates Live Match State.  
* **Logic:**  
  * Enforces the **Telemetry Resolution Gate** (ADR 0008), degrading safely if incoming Tick frequency is \< 10Hz.  
  * Strictly bounds aggregation to the **Active Gameplay Phase** (ADR 0004), pausing on goals/replays.  
  * Monitors **Active Player State**, suspending a player's aggregation if bDemolished: true (ADR 0005).  
  * Calculates boost pickups via the **Boost Delta Heuristic** (ADR 0006\) and **Ambiguous Cap Resolution** (ADR 0007).

**Deep Module 2: The Insight Evaluation Engine**  
This module mathematically determines what data is surfaced to the Presentation Layer, adhering to the **Objective Mirror Philosophy** (ADR 0009).

* **Interface:** evaluateInsights(LiveMatchState, HistoricalBaseline) \-\> returns ActiveInsights.  
* **Logic:**  
  * Applies **Z-Score Normalization** (ADR 0010\) across all disparate metric types to establish mathematical weight.  
  * Executes the **Insight Hierarchy** (ADR 0003), falling back through baseline configurations.  
  * Passes results through the **Categorical Cooldown Matrix** (ADR 0011\) to ensure UI variety among the three categories: Impact & Playmaking, Resource Management, and Pace & Positioning.

**Deep Module 3: The Lifecycle Resolver**  
This module manages the asynchronous chaos of match terminations.

* **Interface:** resolveMatch(websocketBuffer, liveMatchState) \-\> triggers SQLite insertion.  
* **Logic:**  
  * Runs the **Match Validity Gate** (ADR 0012\) to abort cancelled or un-started matches.  
  * Executes the **Final Snapshot Merge** (ADR 0012\) to bypass overtime race conditions by plucking the final scoreboard state from the raw API buffer.  
  * Uses the **Early Termination Resolver** (ADR 0013\) to diff player arrays on MatchDestroyed, assigning strict W/L penalties for Rage Quits vs. Safe Abandons.

**Deep Module 4: State Synchronization**

* **Live Session State (ADR 0014):** Rolling session metrics are kept entirely in memory to prevent repetitive, heavy SQLite reads between consecutive matches.  
* **WebSocket Hydration Handshake (ADR 0015):** The Presentation Layer must emit a RequestSync event on mount/reconnect. The Engine responds with the entire Live Match State and Live Session State, allowing the transient UI to instantly rebuild itself mid-game.

**Deep Module 5: File I/O & Network Queue**

* **Interface:** watchDirectory() \-\> pushes to UploadQueue \-\> emits UploadStatus.  
* **Logic:** A File System Watcher (chokidar) is scoped to the TAGame/Demos folder. It utilizes awaitWriteFinish stabilization to guarantee the game has released the write-lock before acting (ADR 0019). Discovered files are pushed to an **Asynchronous Replay Upload Queue** (ADR 0018\) to ensure HTTP latency to ballchasing.com never blocks the main telemetry event loop.

**Deep Module 6: Configuration & Security Manager** This module handles all operations requiring OS-level privileges and securely manages user secrets.

* **Interface:** configureApi(), storeApiKey(), getApiKey().  
* **Logic:**  
  * **TOCTOU-Safe Configuration:** When attempting to modify DefaultStatsAPI.ini, the Engine strictly avoids the "check-then-act" anti-pattern (using fs.access before fs.writeFile). Instead, it attempts to directly open a write handle (fs.open). If it catches an EACCES (Permission Denied) error, it delegates to the System Tray Dashboard to prompt the user.  
  * **Elevated Delegation:** If the user opts to grant permissions, the Engine spawns an isolated, elevated subprocess (using the Windows runas verb) whose sole responsibility is to execute an atomic write to the .ini file and immediately terminate, preventing the main background process from running with unnecessary permanent administrator privileges.  
  * **Secure Credential Storage:** The ballchasing.com API key is encrypted and stored via the native Windows Credential Manager (DPAPI). It is never written to a local configuration JSON or the SQLite database.

**Database Schema & Strategy**

* **Strict Indexing (ADR 0016):** The SQLite Matches table will utilize a composite index on (GameMode, Timestamp DESC). This guarantees the Engine can fetch the 50-game Historical Baseline in milliseconds, completely future-proofing against infinitely growing databases. Data is strictly preserved; no automated deletion policies will be implemented.

## **Testing Decisions**

* **Event-Sourcing Emulation:** The Telemetry Ingestion Pipeline and Lifecycle Resolver must be tested by mocking the WebSocket and playing back raw JSON arrays of UpdateState Ticks simulating specific edge cases (e.g., an Overtime Goal followed instantly by a rage quit). The test will assert the final output generated for the SQLite insertion.  
* **Z-Score Integrity Validation:** The Insight Evaluation Engine will be tested in strict isolation. Mock baselines and Mock Live States will be provided to assert that the module correctly applies the Categorical Cooldown Matrix and mathematically ranks standard deviations without human bias.  
* **Hydration Simulation:** The WebSocket Hydration Handshake will be tested by simulating a Presentation Layer disconnect/reconnect halfway through a mocked stream of Ticks, asserting the UI accurately rebuilds its state without missing the current score.  
* **Security & Permission Simulation:** The Configuration Manager will be tested against dummy read-only files to ensure EACCES errors correctly bubble up to the UI layer without crashing the background service, verifying the TOCTOU-safe implementation.

## **Out of Scope**

* **Electron / Win32 Overlay:** Explicitly rejected in favor of a UWP Game Bar Widget to maintain zero-ban-risk compliance (ADR 0001).  
* **Overwolf Integration:** Rejected to prevent third-party ecosystem lock-in.  
* **Historical Charting UI:** The application will not render complex historical line graphs or deep analytics for past matches. This is formally delegated to ballchasing.com. The local System Tray Dashboard is restricted to Settings and Encounter History (ADR 0017).  
* **Opinionated Coaching / User Feedback Weights:** The Engine will not alter Z-score math based on subjective user upvotes or arbitrary game-flow multipliers. It remains a strict Objective Mirror (ADR 0009).  
* **Manual Polling for Replays:** Rejected in favor of an event-driven File System Watcher to avoid write-lock corruption and timer race conditions (ADR 0019).

## **Further Notes**

* **Session Hydration Risk:** Because Live Session State is in memory, an Engine crash mid-session wipes it. A boot-sequence routine must be written to query the database and re-hydrate the most recent session's matches into RAM if the timestamp delta indicates the session is still active.