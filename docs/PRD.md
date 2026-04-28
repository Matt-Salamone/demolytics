# **Product Requirements Document (PRD)**

**Project Name:** Demolytics (Rocket League Stats Companion) **Document Version:** 0.4 (Draft)  
**Platform:** Windows Desktop (Second Monitor Overlay)  
**Tech Stack:** Python, CustomTkinter (UI), SQLite (Database), WebSockets

## **1\. Product Objective**

To create a lightweight, low-resource desktop companion app that runs alongside Rocket League. The app will consume live data via the new Rocket League Stats API (WebSocket) to display live session performance, track historical personal data, and build a database of global opponent/teammate statistics for contextual comparison.

## **2\. Core Features & Requirements**

### **2.1 Setup & API Detection (First-Time User Experience)**

The app must ensure the user has the required Rocket League Stats API enabled before attempting to connect.

* **Install Directory Auto-Detection:** On startup, the app will attempt to locate the Rocket League installation directory (checking standard Steam and Epic Games registry paths).  
* **API Configuration Check:** The app will read \<Install Dir\>\\TAGame\\Config\\DefaultStatsAPI.ini and check the PacketSendRate value.  
* **Setup Prompt:** If PacketSendRate is 0 (or the file doesn't exist), the app will display a blocking "Setup Required" screen. This screen will provide the user with clear instructions on how to edit the .ini file to enable the WebSocket, and note that the game must be restarted for changes to take effect.

### **2.2 Live Session Tracking (The Dashboard)**

The app must display real-time updates for the current gaming session.

* **Live Match Stats:** Display current game stats.  
* **Toggleable Stats Display:** Due to the high volume of stats being tracked, the user will have a settings menu to toggle which specific stats are visible on the main dashboard to prevent UI clutter.  
* **Strict Session Boundaries:** A "Session" is defined as a continuous run of matches within the *same* game mode. If the player changes game modes (e.g., switches from 2v2 to 3v3), the app will automatically end the current session and begin a new one. Closing the game will also end the current session.  
* **Session W/L Record:** Track Wins and Losses for the current session. Updates automatically upon the MatchEnded event.  
* **Session Averages:** Calculate and display the user's average stats across all matches played in the *current* session, filtered by the current game mode.

### **2.3 Game Mode Context**

Because stats vary wildly depending on the playlist (e.g., goals per game in 1v1 vs 3v3), all data tracking and comparisons must be isolated by game mode.

* **Auto-Detect Game Mode:** The app will count the number of distinct PrimaryId entries in the MatchInitialized event to infer the mode (2 players \= 1v1, 4 players \= 2v2, 6 players \= 3v3).  
* **Mode-Specific Filtering:** All session averages, historical averages, and global baselines will automatically filter to match the currently detected game mode.

### **2.4 Comprehensive Stat Tracking**

The app will calculate and track a wide breadth of stats beyond the standard scoreboard, derived from the high-frequency UpdateState API events.

* **Core Stats:** Score, Goals, Assists, Saves, Shots, Shooting Percentage (Goals / Shots), Goals Conceded.  
* **Boost Management:** Boost Per Minute (BPM), Average Boost Amount, Amount Collected (Big vs. Small Pads), Amount Stolen, Time at 0 Boost, Time at 100 Boost, Amount Used While Supersonic, Overfill Amount.  
* **Movement Dynamics:** Average Speed, Total Distance, Time at Slow Speed, Time at Boost Speed, Time at Supersonic Speed, Powerslide Count & Duration.  
* **Air/Ground Positioning:** Time on Ground, Time Low in Air, Time High in Air.  
* **Spatial & Positional Awareness:** Average Distance to Ball, Time Behind Ball, Time In Front of Ball, Time in Defensive Half vs. Offensive Half, Time in Defensive/Neutral/Offensive Thirds.  
* **Aggression:** Demos Inflicted, Demos Taken.

### **2.5 Historical Match History & Personal Stats**

The app will save the final match stats to a local database to track performance over time.

* **Match History View:** A dedicated UI tab displaying a chronological list of previously played matches.  
* **Per-Match Drilldown:** Clicking on a specific match in the history view will open a detailed breakdown of all stats for all players in that specific game.  
* **All-Time Averages:** Display historical averages for core and advanced stats, filtered by game mode.  
* **Trend Indicators:** Visually indicate if the current session's averages are trending up (green arrow) or down (red arrow) compared to all-time averages for that game mode.

### **2.6 Global/Community Stat Tracking**

To provide context, the app will silently record the stats of *every* player encountered in every match.

* **The "Average Player" Baseline:** Calculate the average stats of all recorded players (excluding the user) to create a baseline for the specific game mode being played.  
* **Comparative Analysis:** Display the user's all-time averages side-by-side with the "Average Player" baseline (e.g., "Your BPM: 450 | Average 2v2 Player: 410").  
* **Player Encounter History:** The app will track the PrimaryId of all players in a match to build a relationship history. The user can view how many times they have played with a specific person as a teammate versus how many times they have faced them as an opponent.

## **3\. Data Architecture (SQLite)**

To support historical tracking and advanced stats, the app will use a local SQLite database with the following core tables:  
**Table 1: Sessions**

* SessionId (Primary Key, UUID)  
* StartTime (Datetime)  
* GameMode (1v1, 2v2, 3v3)

**Table 2: Matches**

* MatchGuid (Primary Key)  
* SessionId (Foreign Key)  
* Timestamp (Date/Time the match was played)  
* InferredGameMode (1v1, 2v2, 3v3 \- based on player count)  
* UserResult (Win/Loss)  
* Duration (Total match length in seconds)

**Table 3: PlayerMatchStats**

* Id (Primary Key, Auto-Increment)  
* MatchGuid (Foreign Key)  
* PrimaryId (Platform ID, used to track unique players and cross-reference encounters)  
* PlayerName  
* IsUser (Boolean \- True if this is the app owner)  
* TeamNum (0 or 1\)  
* **Core:** Score, Goals, Shots, Assists, Saves, Touches, GoalsConceded  
* **Boost:** BPM, AvgBoost, PadsCollectedBig, PadsCollectedSmall, StolenBig, StolenSmall, TimeZeroBoost, TimeFullBoost, BoostUsedSupersonic  
* **Movement:** AvgSpeed, TotalDistance, TimeSlow, TimeBoostSpeed, TimeSupersonic, TimePowerslide  
* **Positioning:** TimeOnGround, TimeLowAir, TimeHighAir  
* **Spatial:** AvgDistToBall, TimeBehindBall, TimeInFrontOfBall, TimeDefHalf, TimeOffHalf, TimeDefThird, TimeNeuThird, TimeOffThird  
* **Aggression:** DemosInflicted, DemosTaken

## **4\. User Interface (UI) Layout (CustomTkinter)**

* **Theme:** Dark mode by default to match a gaming aesthetic.  
* **Top Bar:** Session Record (W \- L), Detected Game Mode (e.g., "Playing 2v2"), and a "Settings" gear icon to toggle visible stats.  
* **Main Dashboard Tab:**  
  * **Left Column:** Real-time stats updating via WebSocket (only showing user-toggled stats).  
  * **Middle Column:** Current Session Averages vs. All-Time Personal Averages.  
  * **Right Column:** User All-Time Averages vs. The Global Recorded Average.  
* **Match History Tab:** A scrollable list of past matches. Clicking a row opens a modal/sub-view showing the full advanced stat breakdown for that specific match.  
* **Encounters Tab:** A searchable list of PlayerNames showing total games played together as teammates and total games played against as opponents.  
* **Settings Modal:** Checkboxes for every tracked stat to allow the user to customize their Main Dashboard view.

## **5\. Event Handling Logic**

* Startup: Execute API Detection check. Initialize SQLite database if it does not exist.  
* MatchInitialized: Determine Game Mode by counting players. If the Game Mode differs from the active Session's mode, close the current Session and create a new SessionId. Create a new Match entry in DB.  
* UpdateState: Feed live data to the UI. Calculate cumulative/derived stats (like Time at Supersonic, Distance to Ball, or BPM) based on delta time between frames and positional vector data.  
* MatchEnded: Determine Win/Loss based on WinnerTeamNum.  
* MatchDestroyed: Commit the final calculated stats for all players to the PlayerMatchStats database table. Update Session, Historical Averages, and Encounter histories in the UI.