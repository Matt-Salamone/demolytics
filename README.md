# Demolytics 🏎️💥

Demolytics is a powerful, local-first Rocket League statistics tracker and dashboard. It provides real-time insights during your matches, tracks your session performance against your all-time averages, and keeps a detailed "Encounters" history of your win rates alongside or against specific players in your lobbies. 

## 📸 Screenshots

*(Replace these placeholder links with actual images of your app)*

![Dashboard / Glance Stats](docs/screenshots/dashboard.png)
> **Dashboard:** Quick glance at your current session record, win streaks, and automated post-goal insights.

![Live Match & Comparisons](docs/screenshots/live_match.png)
> **Stats Tab:** Live Match tracking (You vs Teams), alongside Session vs All-Time and Encounter Averages.

![Encounters](docs/screenshots/encounters.png)
> **Encounters:** Searchable history of players you've met, detailing your games together and win/loss records as teammates and opponents.

---

## ✨ Features

* **Real-Time Match Dashboard:** View live stats for yourself and team totals as the match progresses.
* **Session vs. All-Time Tracking:** Compare how you are performing today against your historical baseline for the current playlist mode.
* **Encounter History:** Automatically tracks every player in your lobby. See if you've played with them before, and whether they are a better teammate or opponent.
* **Goal Insights:** Quick contextual reads on your stats compared to the lobby after every goal.
* **Ballchasing Auto-Upload:** Automatically uploads your locally saved replays to Ballchasing.com immediately after a match finishes.

---

## 🛑 Prerequisites & Limitations

### Prerequisites
* **Windows PC:** Demolytics relies on Windows Credential Manager for secure token storage and parses standard Windows directory paths.
* **Rocket League (Steam or Epic Games)** 
* **Rocket League Stats API:** Demolytics requires the Stats API to be enabled. The app will help you configure this on the first launch.

### Limitations
* **PC Only:** Because it requires local memory/file reading, it does not support console gameplay.
* **Post-Match Replay Saves:** For the Ballchasing Auto-Upload feature to work, you *must* manually save replays at the post-match screen. 

---

## 🚀 Getting Started

### 1. Installation & First Run
1. Download the latest Demolytics release and run the application.
2. On your first launch, you will see a **Setup Required** screen if the local Stats API is not detected.
3. Click **Enable Stats API (automatic)**. The app will safely patch your `DefaultStatsAPI.ini` file to broadcast events locally.
4. **Restart Rocket League** for the API changes to take effect.

### 2. Setting Up Ballchasing Auto-Upload (Optional)
If you want Demolytics to automatically upload your matches to Ballchasing.com:
1. Open Demolytics and click **Settings**.
2. Navigate to the **Ballchasing** tab.
3. Generate an API token at [Ballchasing.com/doc/api](https://ballchasing.com/doc/api).
4. Paste the token into the app, select your preferred replay visibility (Public, Unlisted, or Private), and check "Automatically upload replays".

---

## 🔒 Privacy & Data Collection: Where does your data go?

Demolytics is built with a strict **local-first** philosophy. We know game trackers can be intrusive, so here is exactly what data is collected and how it is handled:

* **Local Database Only:** All of your match history, performance statistics, and player encounters are stored locally in an SQLite database located at `%APPDATA%\Demolytics\demolytics.db`. **None** of your Rocket League stats or history are ever sent to a third-party cloud or developer server.
* **Local Network API:** The app communicates with Rocket League entirely on your own machine via a local WebSocket (default port `49123`). 
* **File System Monitoring (Replays):** When a match concludes, Demolytics briefly scans your `Documents\My Games\Rocket League\TAGame\Demos` folder (including OneDrive paths if applicable) to locate the newest `.replay` file generated in the last few minutes. It does not read or monitor any other files on your system.
* **Secure Token Storage:** Your Ballchasing API token is **not** saved in plain text. It is stored securely using Python's `keyring` module, which leverages the native Windows Credential Manager. 
* **External Network Calls:** The app will only make external outbound internet requests for two reasons:
  1. Checking GitHub for app updates.
  2. Uploading a replay file directly to `https://ballchasing.com/api/v2/upload` (only if you have provided a token and explicitly enabled Auto-Upload).

You can fully wipe all tracked performance stats or delete your entire database directly from the "Data" tab in the Settings menu at any time.