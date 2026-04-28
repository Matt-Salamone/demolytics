# Manual Validation Checklist

Use this checklist after installing Demolytics in a Windows environment with Rocket League installed.

## Setup Detection

1. Launch Demolytics with `PacketSendRate=0` or no `DefaultStatsAPI.ini`.
2. Confirm the setup screen appears and explains how to enable `PacketSendRate`.
3. Set `PacketSendRate=20` in `TAGame\Config\DefaultStatsAPI.ini`.
4. Restart Rocket League.
5. Relaunch or retry detection in Demolytics and confirm the dashboard opens.

## Live Ingestion

1. Start Rocket League and join an online match.
2. Confirm the connection status changes from waiting to connected.
3. Confirm the detected mode changes to `1v1`, `2v2`, or `3v3` after player data arrives.
4. Confirm live stats update during the match without freezing the UI.

## Session Boundaries

1. Finish a match and confirm the session win/loss record updates after `MatchEnded`.
2. Play another match in the same mode and confirm the same session continues.
3. Change game mode and confirm a new session starts.

## Persistence

1. Leave a completed match and confirm it appears in Match History.
2. Open match details and confirm all players in the match are shown.
3. Confirm the Dashboard shows session averages, all-time averages, and encountered-player baselines.
4. Confirm Encounters lists teammates and opponents after at least one saved match.

## Settings

1. Open Settings and hide several stats.
2. Save settings and confirm the dashboard columns rebuild with only selected supported stats.
3. Relaunch the app and confirm visible stat preferences persist.
