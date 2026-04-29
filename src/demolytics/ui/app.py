from __future__ import annotations

from queue import Queue
from tkinter import BooleanVar, messagebox
from typing import Any

import customtkinter as ctk

from demolytics.api.stats_client import StatsApiClient, StatsApiThread, drain_queue
from demolytics.config.rocket_league import (
    check_stats_api_status,
    setup_instructions,
)
from demolytics.db.repository import DemolyticsRepository
from demolytics.domain.aggregator import (
    DashboardSnapshot,
    DemolyticsAggregator,
    PlayerStatsSnapshot,
)
from demolytics.domain.events import StatsEvent
from demolytics.domain.stats import (
    GLANCE_STAT_KEYS,
    STAT_DEFINITIONS,
    STAT_LABELS,
    SUPPORTED_STAT_KEYS,
    team_stat_suffix,
)
from demolytics.settings import AppSettings, DEFAULT_GLANCE_STATS, save_settings

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

GLANCE_ICONS: dict[str, str] = {
    "shooting_percentage": "🎯",
    "demos_inflicted": "💥",
    "demos_taken": "🛡️",
    "avg_boost": "⚡",
    "avg_speed": "🏎️",
    "airborne_percentage": "✈️",
    "team_demos_inflicted": "💥",
    "team_demos_taken": "🛡️",
    "team_shooting_percentage": "🎯",
    "team_avg_boost": "⚡",
    "team_avg_speed": "🏎️",
    "team_airborne_percentage": "✈️",
    "team_time_zero_boost": "🔋",
    "team_score": "🏁",
    "team_goals": "⚽",
}


class DemolyticsApp(ctk.CTk):
    def __init__(self, settings: AppSettings, repository: DemolyticsRepository) -> None:
        super().__init__()
        self.settings = settings
        self.repository = repository
        self.aggregator = DemolyticsAggregator()
        self.event_queue: Queue[StatsEvent] = Queue()
        self.api_thread: StatsApiThread | None = None
        self.snapshot = self.aggregator.snapshot()
        self.stat_value_labels: dict[str, ctk.CTkLabel] = {}
        self.session_average_labels: dict[str, ctk.CTkLabel] = {}
        self.global_average_labels: dict[str, ctk.CTkLabel] = {}
        self.glance_value_labels: dict[str, ctk.CTkLabel] = {}
        self.glance_session_label: ctk.CTkLabel | None = None
        self.glance_streak_label: ctk.CTkLabel | None = None
        self.glance_goal_insight_label: ctk.CTkLabel | None = None
        self.lobby_encounters_frame: ctk.CTkScrollableFrame | None = None
        self._lobby_encounter_cache_ids: tuple[str, ...] | None = None
        self._lobby_encounter_cache_lines: list[str] = []
        self._lobby_encounter_ui_signature: str | None = None
        self._lobby_session_id_for_encounters: str | None = None
        self.history_rows: list[ctk.CTkFrame] = []
        self.encounter_rows: list[ctk.CTkFrame] = []

        self.title("Demolytics")
        self.geometry("1180x760")
        self.minsize(960, 620)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_for_current_setup_state()

    def _build_for_current_setup_state(self) -> None:
        self._clear_root()
        status = check_stats_api_status(self.settings.install_dir)
        if status.enabled:
            self.settings.websocket_port = status.port
            self.settings.install_dir = str(status.install_dir) if status.install_dir else None
            save_settings(self.settings)
            self._build_main_layout()
            self._start_ingestion()
            self._poll_queues()
            return
        self._build_setup_screen(status)

    def _build_setup_screen(self, status: Any) -> None:
        frame = ctk.CTkFrame(self, corner_radius=12)
        frame.pack(fill="both", expand=True, padx=28, pady=28)

        ctk.CTkLabel(
            frame,
            text="Setup Required",
            font=ctk.CTkFont(size=28, weight="bold"),
        ).pack(anchor="w", padx=28, pady=(28, 8))
        ctk.CTkLabel(frame, text=status.reason, wraplength=850, justify="left").pack(
            anchor="w",
            padx=28,
            pady=(0, 18),
        )

        instructions = setup_instructions(status)
        instructions_text = "\n".join(f"{index}. {line}" for index, line in enumerate(instructions, 1))
        ctk.CTkTextbox(frame, height=180, wrap="word").pack(fill="x", padx=28, pady=8)
        textbox = frame.winfo_children()[-1]
        if isinstance(textbox, ctk.CTkTextbox):
            textbox.insert("1.0", instructions_text)
            textbox.configure(state="disabled")

        ctk.CTkLabel(
            frame,
            text="Rocket League must be restarted after changing DefaultStatsAPI.ini.",
            text_color="#d6d6d6",
        ).pack(anchor="w", padx=28, pady=(8, 18))

        actions = ctk.CTkFrame(frame, fg_color="transparent")
        actions.pack(anchor="w", padx=28, pady=8)
        ctk.CTkButton(actions, text="Retry Detection", command=self._build_for_current_setup_state).pack(
            side="left",
            padx=(0, 12),
        )
        ctk.CTkButton(
            actions,
            text="Open Dashboard Anyway",
            command=self._open_dashboard_without_api_detection,
        ).pack(side="left")

    def _open_dashboard_without_api_detection(self) -> None:
        self._clear_root()
        self._build_main_layout()
        self._start_ingestion()
        self._poll_queues()

    def _build_main_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top_bar = ctk.CTkFrame(self, corner_radius=0)
        top_bar.grid(row=0, column=0, sticky="ew")
        top_bar.grid_columnconfigure(4, weight=1)

        self.record_label = ctk.CTkLabel(top_bar, text="Session: 0 - 0", font=ctk.CTkFont(weight="bold"))
        self.record_label.grid(row=0, column=0, padx=16, pady=12)
        self.mode_label = ctk.CTkLabel(top_bar, text="Mode: unknown")
        self.mode_label.grid(row=0, column=1, padx=16, pady=12)
        self.connection_label = ctk.CTkLabel(top_bar, text="Waiting for Rocket League")
        self.connection_label.grid(row=0, column=2, padx=16, pady=12)
        ctk.CTkButton(top_bar, text="Settings", width=100, command=self._open_settings).grid(
            row=0,
            column=5,
            padx=16,
            pady=10,
        )

        self.tab_view = ctk.CTkTabview(self)
        self.tab_view.grid(row=1, column=0, sticky="nsew", padx=16, pady=16)
        self.glance_tab = self.tab_view.add("Dashboard")
        self.stats_tab = self.tab_view.add("Stats")
        self.history_tab = self.tab_view.add("Match History")
        self.encounters_tab = self.tab_view.add("Encounters")

        self._build_glance_dashboard_tab()
        self._build_stats_tab()
        self._build_history_tab()
        self._build_encounters_tab()
        self._refresh_all_views()

    def _build_glance_dashboard_tab(self) -> None:
        self.glance_tab.grid_columnconfigure(0, weight=1)
        for row in (0, 1, 3):
            self.glance_tab.grid_rowconfigure(row, weight=0)
        self.glance_tab.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self.glance_tab, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        big_font = ctk.CTkFont(size=28, weight="bold")
        self.glance_session_label = ctk.CTkLabel(header, text="Session 0 - 0", font=big_font)
        self.glance_session_label.pack(side="left", padx=(0, 28))
        streak_font = ctk.CTkFont(size=22, weight="bold")
        self.glance_streak_label = ctk.CTkLabel(header, text="🔥 Win streak 0", font=streak_font)
        self.glance_streak_label.pack(side="left")

        insight_wrap = ctk.CTkFrame(self.glance_tab, fg_color=("gray90", "gray25"), corner_radius=10)
        insight_wrap.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        insight_wrap.grid_columnconfigure(0, weight=1)
        self.glance_goal_insight_label = ctk.CTkLabel(
            insight_wrap,
            text=(
                "After each goal, a standout lobby or team stat appears here "
                "(needs ~15s of match time on the clock)."
            ),
            font=ctk.CTkFont(size=15),
            wraplength=1020,
            justify="left",
            anchor="w",
            text_color=("gray30", "gray80"),
        )
        self.glance_goal_insight_label.grid(row=0, column=0, sticky="ew", padx=14, pady=12)

        stats_area = ctk.CTkFrame(self.glance_tab, fg_color="transparent")
        stats_area.grid(row=2, column=0, sticky="nsew", padx=16, pady=8)
        max_cols = 2
        col = row = 0
        self.glance_value_labels.clear()
        for stat_key in self.settings.glance_stats:
            if stat_key not in GLANCE_STAT_KEYS:
                continue
            stats_area.grid_rowconfigure(row, weight=1)
            stats_area.grid_columnconfigure(col, weight=1)
            cell = ctk.CTkFrame(stats_area, fg_color=("gray85", "gray20"), corner_radius=12)
            cell.grid(row=row, column=col, sticky="nsew", padx=10, pady=10)
            icon = GLANCE_ICONS.get(stat_key, "📊")
            ctk.CTkLabel(
                cell,
                text=f"{icon}  {STAT_LABELS[stat_key]}",
                font=ctk.CTkFont(size=15),
                anchor="w",
            ).pack(anchor="w", padx=14, pady=(12, 4))
            value_label = ctk.CTkLabel(
                cell,
                text="--",
                font=ctk.CTkFont(size=26, weight="bold"),
                anchor="w",
            )
            value_label.pack(anchor="w", padx=14, pady=(0, 14))
            self.glance_value_labels[stat_key] = value_label
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

        lobby_section = ctk.CTkFrame(self.glance_tab, fg_color="transparent")
        lobby_section.grid(row=3, column=0, sticky="ew", padx=20, pady=(8, 16))
        lobby_section.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            lobby_section,
            text="👥 This lobby — prior games with you",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.lobby_encounters_frame = ctk.CTkScrollableFrame(lobby_section, height=160)
        self.lobby_encounters_frame.grid(row=1, column=0, sticky="nsew")
        lobby_section.grid_rowconfigure(1, weight=1)

    def _build_stats_tab(self) -> None:
        self.stats_tab.grid_columnconfigure((0, 1, 2), weight=1, uniform="stats_tab")
        self.stats_tab.grid_rowconfigure(0, weight=1)

        live_frame = self._stats_column("Live Match", 0)
        session_frame = self._stats_column("Session vs All-Time", 1)
        global_frame = self._stats_column("You vs Encountered Average", 2)

        for stat_key in self.settings.visible_stats:
            if stat_key not in SUPPORTED_STAT_KEYS:
                continue
            self.stat_value_labels[stat_key] = self._stat_row(live_frame, STAT_LABELS[stat_key], "--")
            self.session_average_labels[stat_key] = self._stat_row(
                session_frame,
                STAT_LABELS[stat_key],
                "-- / --",
            )
            self.global_average_labels[stat_key] = self._stat_row(
                global_frame,
                STAT_LABELS[stat_key],
                "-- / --",
            )

    def _stats_column(self, title: str, column: int) -> ctk.CTkScrollableFrame:
        frame = ctk.CTkScrollableFrame(self.stats_tab, label_text=title)
        frame.grid(row=0, column=column, sticky="nsew", padx=8, pady=8)
        frame.grid_columnconfigure(1, weight=1)
        return frame

    def _stat_row(self, parent: ctk.CTkScrollableFrame, label: str, value: str) -> ctk.CTkLabel:
        row = parent.grid_size()[1]
        ctk.CTkLabel(parent, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        value_label = ctk.CTkLabel(parent, text=value, anchor="e", font=ctk.CTkFont(weight="bold"))
        value_label.grid(row=row, column=1, sticky="e", padx=8, pady=4)
        return value_label

    def _build_history_tab(self) -> None:
        self.history_tab.grid_columnconfigure(0, weight=1)
        self.history_tab.grid_rowconfigure(1, weight=1)
        ctk.CTkButton(
            self.history_tab,
            text="Refresh History",
            command=self._refresh_history,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.history_frame = ctk.CTkScrollableFrame(self.history_tab)
        self.history_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self.history_frame.grid_columnconfigure(0, weight=1)

    def _build_encounters_tab(self) -> None:
        self.encounters_tab.grid_columnconfigure(0, weight=1)
        self.encounters_tab.grid_rowconfigure(1, weight=1)
        controls = ctk.CTkFrame(self.encounters_tab, fg_color="transparent")
        controls.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        self.encounter_search = ctk.CTkEntry(controls, placeholder_text="Search player name")
        self.encounter_search.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.encounter_search.bind("<KeyRelease>", lambda _event: self._refresh_encounters())
        ctk.CTkButton(controls, text="Refresh", command=self._refresh_encounters).pack(side="left")
        self.encounters_frame = ctk.CTkScrollableFrame(self.encounters_tab)
        self.encounters_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self.encounters_frame.grid_columnconfigure(0, weight=1)

    def _start_ingestion(self) -> None:
        if self.api_thread is not None:
            return
        client = StatsApiClient(port=self.settings.websocket_port)
        self.api_thread = StatsApiThread(client, self.event_queue)
        self.api_thread.start()

    def _poll_queues(self) -> None:
        if self.api_thread is not None:
            for status in drain_queue(self.api_thread.status_queue):
                self.connection_label.configure(text=str(status))

        changed = False
        for event in drain_queue(self.event_queue):
            result = self.aggregator.handle_event(event)
            self.snapshot = result.snapshot
            if self.snapshot.session:
                self.repository.upsert_session(self.snapshot.session)
            if result.completed_match is not None:
                self.repository.save_completed_match(result.completed_match)
                self._refresh_history()
                self._refresh_encounters()
            changed = True

        if changed:
            self._refresh_stats_tab(self.snapshot)
            self._refresh_glance_dashboard(self.snapshot)
        self.after(250, self._poll_queues)

    def _refresh_all_views(self) -> None:
        self._refresh_stats_tab(self.snapshot)
        self._refresh_glance_dashboard(self.snapshot)
        self._refresh_history()
        self._refresh_encounters()

    def _refresh_glance_dashboard(self, snapshot: DashboardSnapshot) -> None:
        if self.glance_session_label is None or self.glance_streak_label is None:
            return
        if snapshot.session:
            wins, losses = snapshot.session.wins, snapshot.session.losses
            self.glance_session_label.configure(text=f"Session {wins} - {losses}")
            if wins > losses:
                self.glance_session_label.configure(text_color="#3dd68c")
            elif losses > wins:
                self.glance_session_label.configure(text_color="#ff6b6b")
            else:
                self.glance_session_label.configure(text_color=("gray10", "gray90"))
        else:
            self.glance_session_label.configure(text="Session 0 - 0", text_color=("gray10", "gray90"))

        self.glance_streak_label.configure(text=f"🔥 Win streak {snapshot.win_streak}")

        if self.glance_goal_insight_label is not None:
            if snapshot.goal_insight:
                self.glance_goal_insight_label.configure(
                    text=snapshot.goal_insight,
                    text_color=("gray10", "gray90"),
                    font=ctk.CTkFont(size=15, weight="bold"),
                )
            else:
                self.glance_goal_insight_label.configure(
                    text=(
                        "After each goal, a standout lobby or team stat appears here "
                        "(needs ~15s of match time on the clock)."
                    ),
                    text_color=("gray30", "gray80"),
                    font=ctk.CTkFont(size=15),
                )

        for stat_key, label in self.glance_value_labels.items():
            raw = _glance_stat_raw(snapshot, stat_key)
            label.configure(text=_format_stat(stat_key, raw))

        self._refresh_lobby_encounters(snapshot)

    def _refresh_lobby_encounters(self, snapshot: DashboardSnapshot) -> None:
        if self.lobby_encounters_frame is None:
            return

        current_sid = snapshot.session.session_id if snapshot.session else None
        if current_sid != self._lobby_session_id_for_encounters:
            self._lobby_session_id_for_encounters = current_sid
            self._lobby_encounter_cache_ids = None
            self._lobby_encounter_cache_lines = []
            self._lobby_encounter_ui_signature = None

        others = [p for p in snapshot.live_players if not p.is_user and p.primary_id]
        if others:
            ids = tuple(sorted(p.primary_id for p in others))
            db_rows = self.repository.get_encounters_for_primary_ids(ids)
            lines: list[str] = []
            for player in sorted(others, key=lambda p: (p.player_name or "").lower()):
                row = db_rows.get(player.primary_id)
                teammate = int(row["teammate_games"]) if row is not None else 0
                opponent = int(row["opponent_games"]) if row is not None else 0
                total = teammate + opponent
                name = (player.player_name or "").strip() or player.primary_id
                if total == 0:
                    lines.append(f"{name}  —  no prior matches recorded")
                else:
                    lines.append(
                        f"{name}  —  {total} prior games (teammate {teammate}, opponent {opponent})"
                    )
            self._lobby_encounter_cache_ids = ids
            self._lobby_encounter_cache_lines = list(lines)
            signature = f"live:{','.join(ids)}"
        elif self._lobby_encounter_cache_lines and self._lobby_encounter_cache_ids is not None:
            lines = list(self._lobby_encounter_cache_lines)
            signature = f"cache:{','.join(self._lobby_encounter_cache_ids)}"
        else:
            lines = []
            signature = "placeholder"

        if signature == self._lobby_encounter_ui_signature:
            return

        self._lobby_encounter_ui_signature = signature
        for child in self.lobby_encounters_frame.winfo_children():
            child.destroy()

        if not lines:
            ctk.CTkLabel(
                self.lobby_encounters_frame,
                text="Join a match to see how often you have played with each lobby player.",
                font=ctk.CTkFont(size=14),
                text_color="gray",
                wraplength=900,
                justify="left",
                anchor="w",
            ).pack(anchor="w", padx=8, pady=6)
            return

        for line in lines:
            ctk.CTkLabel(
                self.lobby_encounters_frame,
                text=line,
                font=ctk.CTkFont(size=15),
                anchor="w",
                justify="left",
            ).pack(anchor="w", padx=8, pady=4)

    def _refresh_stats_tab(self, snapshot: DashboardSnapshot) -> None:
        if snapshot.session:
            self.record_label.configure(text=f"Session: {snapshot.session.wins} - {snapshot.session.losses}")
            self.mode_label.configure(text=f"Mode: {snapshot.session.game_mode}")
            game_mode = snapshot.session.game_mode
            session_id = snapshot.session.session_id
        else:
            self.record_label.configure(text="Session: 0 - 0")
            self.mode_label.configure(text=f"Mode: {snapshot.current_game_mode}")
            game_mode = None if snapshot.current_game_mode == "unknown" else snapshot.current_game_mode
            session_id = None

        for stat_key, label in self.stat_value_labels.items():
            if stat_key.startswith("team_"):
                inner = team_stat_suffix(stat_key)
                user_val = snapshot.user_team_stats.get(inner)
                lines = [_format_stat(stat_key, user_val)]
                for team in sorted(snapshot.live_teams, key=lambda t: t.team_num):
                    team_label = team.team_name or f"Team {team.team_num}"
                    tv = team.stats.get(inner)
                    lines.append(f"{team_label}: {_format_stat(stat_key, tv)}")
            else:
                user_val = snapshot.live_user_stats.get(stat_key)
                lines = [_format_stat(stat_key, user_val)]
                for team in sorted(snapshot.live_teams, key=lambda t: t.team_num):
                    team_label = team.team_name or f"Team {team.team_num}"
                    tv = team.stats.get(stat_key)
                    lines.append(f"{team_label}: {_format_stat(stat_key, tv)}")
            label.configure(text="\n".join(lines), justify="right")

        session_averages = self.repository.get_user_averages(game_mode=game_mode, session_id=session_id)
        all_time_averages = self.repository.get_user_averages(game_mode=game_mode)
        global_baseline = self.repository.get_global_baseline(game_mode=game_mode)

        for stat_key, label in self.session_average_labels.items():
            value = _format_comparison(stat_key, session_averages.get(stat_key), all_time_averages.get(stat_key))
            label.configure(text=value)
        for stat_key, label in self.global_average_labels.items():
            user_value = all_time_averages.get(stat_key)
            global_value = global_baseline.get(stat_key)
            label.configure(text=_format_comparison(stat_key, user_value, global_value))

    def _refresh_history(self) -> None:
        for row in self.history_rows:
            row.destroy()
        self.history_rows.clear()

        matches = self.repository.list_matches()
        if not matches:
            label = ctk.CTkLabel(self.history_frame, text="No completed matches yet.")
            label.grid(row=0, column=0, sticky="w", padx=8, pady=8)
            self.history_rows.append(label)
            return

        for index, match in enumerate(matches):
            row = ctk.CTkFrame(self.history_frame)
            row.grid(row=index, column=0, sticky="ew", padx=4, pady=4)
            row.grid_columnconfigure(0, weight=1)
            text = (
                f"{match['timestamp']}  |  {match['inferred_game_mode']}  |  "
                f"{match['user_result'] or 'Unknown'}  |  {match['duration_seconds']:.0f}s"
            )
            ctk.CTkLabel(row, text=text, anchor="w").grid(row=0, column=0, sticky="ew", padx=8, pady=8)
            ctk.CTkButton(
                row,
                text="Details",
                width=84,
                command=lambda guid=match["match_guid"]: self._show_match_details(guid),
            ).grid(row=0, column=1, padx=8, pady=8)
            self.history_rows.append(row)

    def _refresh_encounters(self) -> None:
        for row in self.encounter_rows:
            row.destroy()
        self.encounter_rows.clear()

        search = self.encounter_search.get().lower() if hasattr(self, "encounter_search") else ""
        encounters = [
            row for row in self.repository.list_encounters() if search in str(row["player_name"]).lower()
        ]
        if not encounters:
            label = ctk.CTkLabel(self.encounters_frame, text="No encounters yet.")
            label.grid(row=0, column=0, sticky="w", padx=8, pady=8)
            self.encounter_rows.append(label)
            return

        for index, encounter in enumerate(encounters):
            row = ctk.CTkFrame(self.encounters_frame)
            row.grid(row=index, column=0, sticky="ew", padx=4, pady=4)
            row.grid_columnconfigure(0, weight=1)
            text = (
                f"{encounter['player_name']}  |  "
                f"Teammate: {encounter['teammate_games']}  |  "
                f"Opponent: {encounter['opponent_games']}"
            )
            ctk.CTkLabel(row, text=text, anchor="w").grid(row=0, column=0, sticky="ew", padx=8, pady=8)
            self.encounter_rows.append(row)

    def _show_match_details(self, match_guid: str) -> None:
        detail = ctk.CTkToplevel(self)
        detail.title("Match Details")
        detail.geometry("760x540")
        frame = ctk.CTkScrollableFrame(detail, label_text=match_guid)
        frame.pack(fill="both", expand=True, padx=16, pady=16)
        players = self.repository.get_match_players(match_guid)
        for index, player in enumerate(players):
            text = _player_detail_text(player)
            ctk.CTkLabel(frame, text=text, justify="left", anchor="w").grid(
                row=index,
                column=0,
                sticky="ew",
                padx=8,
                pady=8,
            )

    def _open_settings(self) -> None:
        modal = ctk.CTkToplevel(self)
        modal.title("Settings")
        modal.geometry("480x720")
        modal.grab_set()

        outer = ctk.CTkScrollableFrame(modal)
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(outer, text="Glance dashboard (during game)", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w",
            pady=(0, 6),
        )
        glance_vars: dict[str, BooleanVar] = {}
        for stat in STAT_DEFINITIONS:
            if stat.key not in GLANCE_STAT_KEYS:
                continue
            variable = BooleanVar(value=stat.key in self.settings.glance_stats)
            checkbox = ctk.CTkCheckBox(outer, text=stat.label, variable=variable)
            checkbox.pack(anchor="w", padx=8, pady=3)
            glance_vars[stat.key] = variable

        ctk.CTkLabel(outer, text="Stats tab columns", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w",
            pady=(16, 6),
        )
        variables: dict[str, BooleanVar] = {}
        for stat in STAT_DEFINITIONS:
            variable = BooleanVar(value=stat.key in self.settings.visible_stats)
            checkbox = ctk.CTkCheckBox(
                outer,
                text=stat.label if stat.supported else f"{stat.label} (future)",
                variable=variable,
                state="normal" if stat.supported else "disabled",
            )
            checkbox.pack(anchor="w", padx=8, pady=3)
            variables[stat.key] = variable

        def save() -> None:
            self.settings.glance_stats = [
                key for key, variable in glance_vars.items() if variable.get() and key in GLANCE_STAT_KEYS
            ]
            if not self.settings.glance_stats:
                self.settings.glance_stats = list(DEFAULT_GLANCE_STATS)
            self.settings.visible_stats = [
                key for key, variable in variables.items() if variable.get() and key in SUPPORTED_STAT_KEYS
            ]
            save_settings(self.settings)
            modal.destroy()
            self._rebuild_glance_tab()
            self._rebuild_stats_tab()

        btn_row = ctk.CTkFrame(modal, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkButton(btn_row, text="Save", command=save).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row,
            text="Reset all statistics…",
            fg_color="#8b3a3a",
            command=lambda: (modal.destroy(), self._confirm_reset_database()),
        ).pack(
            side="left",
        )

    def _confirm_reset_database(self) -> None:
        confirm = ctk.CTkToplevel(self)
        confirm.title("Reset statistics")
        confirm.geometry("440x220")
        confirm.grab_set()
        ctk.CTkLabel(
            confirm,
            text=(
                "This permanently deletes all saved matches, player stats, sessions, "
                "and encounter history from the local database. This cannot be undone."
            ),
            wraplength=400,
            justify="left",
        ).pack(padx=20, pady=20)

        actions = ctk.CTkFrame(confirm, fg_color="transparent")
        actions.pack(pady=(0, 16))

        def do_reset() -> None:
            self.repository.clear_all_data()
            self.aggregator.reset_tracking_state()
            self.snapshot = self.aggregator.snapshot()
            self._lobby_encounter_cache_ids = None
            self._lobby_encounter_cache_lines = []
            self._lobby_encounter_ui_signature = None
            self._lobby_session_id_for_encounters = None
            confirm.destroy()
            self._refresh_all_views()
            messagebox.showinfo("Demolytics", "All statistics were reset.")

        ctk.CTkButton(actions, text="Cancel", width=100, command=confirm.destroy).pack(side="left", padx=8)
        ctk.CTkButton(
            actions,
            text="Delete everything",
            width=140,
            fg_color="#8b3a3a",
            command=do_reset,
        ).pack(side="left", padx=8)

    def _rebuild_glance_tab(self) -> None:
        for child in self.glance_tab.winfo_children():
            child.destroy()
        self.glance_value_labels.clear()
        self.glance_session_label = None
        self.glance_streak_label = None
        self.glance_goal_insight_label = None
        self.lobby_encounters_frame = None
        self._lobby_encounter_ui_signature = None
        self._build_glance_dashboard_tab()
        self._refresh_glance_dashboard(self.snapshot)

    def _rebuild_stats_tab(self) -> None:
        for child in self.stats_tab.winfo_children():
            child.destroy()
        self.stat_value_labels.clear()
        self.session_average_labels.clear()
        self.global_average_labels.clear()
        self._build_stats_tab()
        self._refresh_stats_tab(self.snapshot)

    def _clear_root(self) -> None:
        if self.api_thread is not None:
            self.api_thread.stop()
            self.api_thread = None
        for child in self.winfo_children():
            child.destroy()

    def _on_close(self) -> None:
        if self.api_thread is not None:
            self.api_thread.stop()
        self.destroy()


def _format_comparison(stat_key: str, left: float | None, right: float | None) -> str:
    return f"{_format_stat(stat_key, left)} / {_format_stat(stat_key, right)}"


def _glance_stat_raw(snapshot: DashboardSnapshot, stat_key: str) -> float | None:
    if stat_key.startswith("team_"):
        return snapshot.user_team_stats.get(team_stat_suffix(stat_key))
    return snapshot.live_user_stats.get(stat_key)


def _format_stat(stat_key: str, value: float | None) -> str:
    if value is None:
        return "--"
    base = team_stat_suffix(stat_key) if stat_key.startswith("team_") else stat_key
    if base.startswith("time_"):
        return f"{value:.1f}s"
    if "percentage" in base:
        return f"{value:.1f}%"
    if base.startswith("avg_"):
        return f"{value:.1f}"
    return f"{value:.0f}"


def _player_detail_text(player: Any) -> str:
    stats = [
        f"{STAT_LABELS[key]}: {_format_stat(key, player[key])}"
        for key in SUPPORTED_STAT_KEYS
        if key in player.keys()
    ]
    user_label = "User" if player["is_user"] else "Player"
    return (
        f"{user_label}: {player['player_name']} ({player['primary_id']})\n"
        f"Team: {player['team_num']}\n"
        + "  |  ".join(stats)
    )
