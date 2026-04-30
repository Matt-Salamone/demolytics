from __future__ import annotations

import logging
import threading
import time
import webbrowser
from datetime import datetime
from queue import Queue
from tkinter import BooleanVar, StringVar, messagebox
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
from demolytics.domain.events import MatchLifecycleEvent, StatsEvent
from demolytics.domain.stats import (
    DEFAULT_STATS_TAB_VISIBLE,
    GLANCE_STAT_KEYS,
    STAT_DEFINITIONS,
    STAT_LABELS,
    STATS_TAB_COLUMN_KEYS,
    SUPPORTED_STAT_KEYS,
    team_stat_suffix,
)
from demolytics.settings import (
    BALLCHASING_VISIBILITY_CHOICES,
    AppSettings,
    DEFAULT_GLANCE_STATS,
    SETTINGS_FORMAT_VERSION,
    STANDARD_PLAYLIST_MODES,
    save_settings,
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

LOGGER = logging.getLogger(__name__)

# Brief pause so RL can finish writing the replay before we look in Demos.
_BALLCHASING_POST_MATCH_DELAY_S = 4.0

_BALLCHASING_NO_REPLAY_SNACKBAR = (
    "Could not find a replay for Ballchasing. Make sure you are saving the replay from the post-match menu."
)


def _normalize_playlist_mode(mode: str) -> str:
    if mode in STANDARD_PLAYLIST_MODES:
        return mode
    return "1v1"


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


def _wl_record_color(wins: int, losses: int) -> str | tuple[str, str]:
    if wins > losses:
        return "#3dd68c"
    if losses > wins:
        return "#ff6b6b"
    return ("gray35", "gray70")


def _encounter_row_vector(row: Any | None) -> str:
    if row is None:
        return "-"
    return (
        f"{int(row['teammate_games'])}:{int(row['opponent_games'])}:"
        f"{int(row['teammate_wins'])}:{int(row['teammate_losses'])}:"
        f"{int(row['opponent_wins'])}:{int(row['opponent_losses'])}"
    )


def _lobby_encounter_stats_signature(
    ids: tuple[str, ...],
    db_rows: dict[str, Any],
    session_rows: dict[str, Any],
    session_key: str,
) -> str:
    parts: list[str] = []
    for pid in sorted(ids):
        parts.append(
            f"{pid}@{_encounter_row_vector(db_rows.get(pid))}@{_encounter_row_vector(session_rows.get(pid))}"
        )
    return f"{session_key}#" + "|".join(parts)


class DemolyticsApp(ctk.CTk):
    def __init__(self, settings: AppSettings, repository: DemolyticsRepository) -> None:
        super().__init__()
        self.settings = settings
        self.repository = repository
        self.aggregator = DemolyticsAggregator(repository=self.repository)
        self.event_queue: Queue[StatsEvent] = Queue()
        self.api_thread: StatsApiThread | None = None
        self.snapshot = self.aggregator.snapshot()
        self.stat_live_personal_labels: dict[str, ctk.CTkLabel] = {}
        self.stat_live_team_labels: dict[str, ctk.CTkLabel] = {}
        self.session_average_labels: dict[str, ctk.CTkLabel] = {}
        self.global_average_labels: dict[str, ctk.CTkLabel] = {}
        self.stats_session_scope_label: ctk.CTkLabel | None = None
        self.stats_global_scope_label: ctk.CTkLabel | None = None
        self.glance_value_labels: dict[str, ctk.CTkLabel] = {}
        self.glance_session_label: ctk.CTkLabel | None = None
        self.glance_streak_label: ctk.CTkLabel | None = None
        self.glance_goal_insight_label: ctk.CTkLabel | None = None
        self.lobby_encounters_frame: ctk.CTkScrollableFrame | None = None
        self._lobby_encounter_cache_ids: tuple[str, ...] | None = None
        self._lobby_encounter_ui_signature: str | None = None
        self._lobby_session_id_for_encounters: str | None = None
        self.history_rows: list[ctk.CTkFrame] = []
        self.encounter_rows: list[ctk.CTkFrame] = []
        self._comparison_mode_var = StringVar(value=_normalize_playlist_mode(settings.comparison_game_mode))
        self._replay_created_stash: dict[str, dict[str, Any]] = {}
        self._ballchasing_queue: Queue[dict[str, Any]] = Queue()
        self._ballchasing_worker_thread: threading.Thread | None = None
        self._snackbar_after_id: str | None = None

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

        self._snackbar_frame = ctk.CTkFrame(self, corner_radius=10)
        self._snackbar_frame.grid_columnconfigure(0, weight=1)
        self._snackbar_label = ctk.CTkLabel(
            self._snackbar_frame,
            text="",
            font=ctk.CTkFont(size=14),
            wraplength=920,
            justify="center",
        )
        self._snackbar_label.grid(row=0, column=0, padx=20, pady=12, sticky="ew")
        self._snackbar_frame.grid_remove()

    def _show_snackbar(self, message: str, *, success: bool) -> None:
        frame = getattr(self, "_snackbar_frame", None)
        label = getattr(self, "_snackbar_label", None)
        if frame is None or label is None:
            return
        try:
            if not frame.winfo_exists():
                return
        except Exception:
            return

        prev = self._snackbar_after_id
        if prev is not None:
            try:
                self.after_cancel(prev)
            except Exception:
                pass
            self._snackbar_after_id = None

        label.configure(text=message)
        if success:
            frame.configure(fg_color=("#14532d", "#166534"))
            label.configure(text_color=("#ecfdf5", "#ecfdf5"))
        else:
            frame.configure(fg_color=("#7f1d1d", "#991b1b"))
            label.configure(text_color=("#fef2f2", "#fef2f2"))

        frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 12))
        self._snackbar_after_id = self.after(5500, self._hide_snackbar)

    def _hide_snackbar(self) -> None:
        self._snackbar_after_id = None
        frame = getattr(self, "_snackbar_frame", None)
        if frame is None:
            return
        try:
            if frame.winfo_exists():
                frame.grid_remove()
        except Exception:
            pass

    def _on_glance_insight_wrap_configure(self, event: Any) -> None:
        if self.glance_goal_insight_label is None:
            return
        try:
            if not self.glance_goal_insight_label.winfo_exists():
                return
            w = int(event.widget.winfo_width())
        except Exception:
            return
        if w > 48:
            self.glance_goal_insight_label.configure(wraplength=max(120, w - 28))

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
                "After each goal, a quick read on your stats compared to the lobby appears here "
                "(needs a few seconds of match time on the clock)."
            ),
            font=ctk.CTkFont(size=15),
            wraplength=400,
            justify="left",
            anchor="w",
            text_color=("gray30", "gray80"),
        )
        self.glance_goal_insight_label.grid(row=0, column=0, sticky="ew", padx=14, pady=12)
        insight_wrap.bind("<Configure>", self._on_glance_insight_wrap_configure)

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
            text="👥 This lobby",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.lobby_encounters_frame = ctk.CTkScrollableFrame(lobby_section, height=160)
        self.lobby_encounters_frame.grid(row=1, column=0, sticky="nsew")
        lobby_section.grid_rowconfigure(1, weight=1)

    def _build_stats_tab(self) -> None:
        self.stats_tab.grid_columnconfigure(0, weight=1)
        self.stats_tab.grid_rowconfigure(0, weight=0)
        self.stats_tab.grid_rowconfigure(1, weight=1)

        self._build_comparison_mode_bar(row=0, column=0)

        self.stats_subtab_view = ctk.CTkTabview(self.stats_tab)
        self.stats_subtab_view.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        live_parent = self.stats_subtab_view.add("Live Match")
        session_parent = self.stats_subtab_view.add("Session vs All-Time")
        global_parent = self.stats_subtab_view.add("You vs Encountered Average")

        for tab_body in (session_parent, global_parent):
            tab_body.grid_columnconfigure(0, weight=1)
            tab_body.grid_rowconfigure(0, weight=1)

        self._build_live_match_panel(live_parent)

        session_frame, self.stats_session_scope_label = self._stats_column(
            session_parent,
            "Session vs All-Time",
            show_mode_scope=True,
        )
        global_frame, self.stats_global_scope_label = self._stats_column(
            global_parent,
            "You vs Encountered Average",
            show_mode_scope=True,
        )

        for stat_key in self.settings.visible_stats:
            if stat_key not in SUPPORTED_STAT_KEYS:
                continue
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

    def _build_comparison_mode_bar(self, row: int, column: int) -> None:
        bar = ctk.CTkFrame(self.stats_tab, fg_color=("gray88", "gray22"), corner_radius=8)
        bar.grid(row=row, column=column, sticky="ew", padx=8, pady=(8, 4))
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(
            inner,
            text="Session & baseline use playlist:",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).pack(side="left", padx=(0, 12))
        for mode in STANDARD_PLAYLIST_MODES:
            ctk.CTkRadioButton(
                inner,
                text=mode,
                variable=self._comparison_mode_var,
                value=mode,
                command=self._on_comparison_mode_user_pick,
            ).pack(side="left", padx=(0, 10))

    def _on_comparison_mode_user_pick(self) -> None:
        mode = _normalize_playlist_mode(self._comparison_mode_var.get())
        if mode != self.settings.comparison_game_mode:
            self.settings.comparison_game_mode = mode
            save_settings(self.settings)
        self._refresh_stats_tab(self.snapshot)

    def _build_live_match_panel(self, parent: ctk.CTkFrame) -> None:
        """Live Match: You and Teams side by side."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        title_font = ctk.CTkFont(size=15, weight="bold")
        ctk.CTkLabel(parent, text="You", font=title_font, anchor="w").grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )
        ctk.CTkLabel(parent, text="Teams", font=title_font, anchor="w").grid(
            row=0, column=1, sticky="w", padx=8, pady=(8, 4)
        )

        personal_scroll = ctk.CTkScrollableFrame(parent)
        personal_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        personal_scroll.grid_columnconfigure(1, weight=1)

        teams_scroll = ctk.CTkScrollableFrame(parent)
        teams_scroll.grid(row=1, column=1, sticky="nsew", padx=8, pady=(0, 8))
        teams_scroll.grid_columnconfigure(1, weight=1)

        self.stat_live_personal_labels.clear()
        self.stat_live_team_labels.clear()

        has_personal = False
        has_team = False
        for stat_key in self.settings.visible_stats:
            if stat_key not in STATS_TAB_COLUMN_KEYS:
                continue
            if stat_key.startswith("team_"):
                has_team = True
                self.stat_live_team_labels[stat_key] = self._stat_row(
                    teams_scroll,
                    STAT_LABELS[stat_key],
                    "--",
                )
            else:
                has_personal = True
                self.stat_live_personal_labels[stat_key] = self._stat_row(
                    personal_scroll,
                    STAT_LABELS[stat_key],
                    "--",
                )

        if not has_personal:
            ctk.CTkLabel(
                personal_scroll,
                text="No player stats enabled. Add them under Stats columns in Settings.",
                wraplength=300,
                anchor="w",
                justify="left",
                text_color=("gray35", "gray70"),
            ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=8)
        if not has_team:
            ctk.CTkLabel(
                teams_scroll,
                text="No team stats enabled. Add them under Stats columns in Settings.",
                wraplength=300,
                anchor="w",
                justify="left",
                text_color=("gray35", "gray70"),
            ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=8)

    def _stats_column(
        self,
        parent: ctk.CTkFrame,
        title: str,
        *,
        show_mode_scope: bool,
    ) -> tuple[ctk.CTkScrollableFrame, ctk.CTkLabel | None]:
        wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        wrapper.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        wrapper.grid_columnconfigure(0, weight=1)

        title_font = ctk.CTkFont(size=15, weight="bold")
        ctk.CTkLabel(wrapper, text=title, font=title_font, anchor="w").grid(
            row=0,
            column=0,
            sticky="w",
            padx=4,
            pady=(0, 2),
        )
        scope_label: ctk.CTkLabel | None = None
        scroll_row = 1
        if show_mode_scope:
            scope_label = ctk.CTkLabel(
                wrapper,
                text="",
                font=ctk.CTkFont(size=12),
                text_color=("gray35", "gray70"),
                anchor="w",
                justify="left",
                wraplength=480,
            )
            scope_label.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 8))
            scroll_row = 2
        wrapper.grid_rowconfigure(scroll_row, weight=1)
        frame = ctk.CTkScrollableFrame(wrapper)
        frame.grid(row=scroll_row, column=0, sticky="nsew")
        frame.grid_columnconfigure(1, weight=1)
        return frame, scope_label

    def _stat_row(self, parent: ctk.CTkFrame, label: str, value: str) -> ctk.CTkLabel:
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
        self.encounters_frame = ctk.CTkScrollableFrame(self.encounters_tab)
        self.encounters_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self.encounters_frame.grid_columnconfigure(0, weight=1)
        controls = ctk.CTkFrame(self.encounters_tab, fg_color="transparent")
        controls.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        self.encounter_search = ctk.CTkEntry(controls, placeholder_text="Search player name")
        self.encounter_search.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.encounter_search.bind("<KeyRelease>", lambda _event: self._refresh_encounters())
        self.encounter_sort_combo = ctk.CTkComboBox(
            controls,
            width=160,
            values=("Recent", "Most games"),
            command=lambda _choice: self._refresh_encounters(),
        )
        self.encounter_sort_combo.set("Recent")
        self.encounter_sort_combo.pack(side="left", padx=(0, 8))
        ctk.CTkButton(controls, text="Refresh", command=self._refresh_encounters).pack(side="left")

    def _start_ingestion(self) -> None:
        if self.api_thread is not None:
            return
        client = StatsApiClient(port=self.settings.websocket_port)
        self.api_thread = StatsApiThread(client, self.event_queue)
        self.api_thread.start()

    def _ensure_ballchasing_worker(self) -> None:
        if self._ballchasing_worker_thread is not None and self._ballchasing_worker_thread.is_alive():
            return
        self._ballchasing_worker_thread = threading.Thread(
            target=self._ballchasing_worker_loop,
            daemon=True,
            name="BallchasingUpload",
        )
        self._ballchasing_worker_thread.start()

    def _maybe_enqueue_ballchasing_upload(
        self,
        *,
        replay_data: dict[str, Any] | None,
        match_end: datetime,
    ) -> None:
        if not self.settings.ballchasing_auto_upload:
            return
        token = self.settings.ballchasing_token.strip()
        if not token:
            LOGGER.debug("Ballchasing auto-upload is enabled but no API token is set; skipping upload.")
            return
        self._ensure_ballchasing_worker()
        self._ballchasing_queue.put(
            {
                "replay_data": dict(replay_data) if replay_data else None,
                "match_end": match_end,
                "token": token,
                "visibility": self.settings.ballchasing_visibility,
            }
        )

    def _ballchasing_worker_loop(self) -> None:
        from demolytics.integrations.ballchasing import BallchasingUploadError, upload_replay_file
        from demolytics.integrations.replay_path import resolve_replay_path

        while True:
            job = self._ballchasing_queue.get()
            try:
                time.sleep(_BALLCHASING_POST_MATCH_DELAY_S)
                replay_path = resolve_replay_path(
                    job.get("replay_data"),
                    job["match_end"],
                )
                if replay_path is None:
                    LOGGER.debug("Ballchasing: no replay file found for upload.")
                    self.after(
                        0,
                        lambda msg=_BALLCHASING_NO_REPLAY_SNACKBAR: self._show_snackbar(msg, success=False),
                    )
                    continue
                replay_id = upload_replay_file(replay_path, job["token"], job["visibility"])
                LOGGER.info("Ballchasing worker: uploaded %s replay_id=%s", replay_path.name, replay_id)
                name = replay_path.name
                self.after(
                    0,
                    lambda n=name: self._show_snackbar(
                        f"Replay uploaded to Ballchasing: {n}",
                        success=True,
                    ),
                )
            except BallchasingUploadError as exc:
                LOGGER.warning("Ballchasing upload failed: %s", exc)
                msg = str(exc)
                self.after(
                    0,
                    lambda m=msg: self._show_snackbar(f"Ballchasing upload failed: {m}", success=False),
                )
            except Exception as exc:
                LOGGER.exception("Ballchasing upload raised unexpectedly")
                msg = str(exc)
                self.after(
                    0,
                    lambda m=msg: self._show_snackbar(
                        f"Ballchasing upload failed: {m}",
                        success=False,
                    ),
                )
            finally:
                self._ballchasing_queue.task_done()

    def _poll_queues(self) -> None:
        if self.api_thread is not None:
            for status in drain_queue(self.api_thread.status_queue):
                self.connection_label.configure(text=str(status))

        changed = False
        for event in drain_queue(self.event_queue):
            if isinstance(event, MatchLifecycleEvent) and event.event_name == "ReplayCreated" and event.match_guid:
                self._replay_created_stash[event.match_guid] = dict(event.data)
            result = self.aggregator.handle_event(event)
            self.snapshot = result.snapshot
            if self.snapshot.session:
                self.repository.upsert_session(self.snapshot.session)
            if result.completed_match is not None:
                self.repository.save_completed_match(result.completed_match)
                self._refresh_history()
                self._refresh_encounters()
                replay_snapshot = self._replay_created_stash.pop(result.completed_match.match_guid, None)
                self._maybe_enqueue_ballchasing_upload(
                    replay_data=replay_snapshot,
                    match_end=result.completed_match.timestamp,
                )
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
                        "After each goal, a quick read on your stats compared to the lobby appears here "
                        "(needs a few seconds of match time on the clock)."
                    ),
                    text_color=("gray30", "gray80"),
                    font=ctk.CTkFont(size=15),
                )

        for stat_key, label in self.glance_value_labels.items():
            raw = _glance_stat_raw(snapshot, stat_key)
            label.configure(text=_format_stat(stat_key, raw))

        self._refresh_lobby_encounters(snapshot)

    def _pack_lobby_player_row(
        self,
        parent: ctk.CTkScrollableFrame,
        display_name: str,
        row_all: Any | None,
        row_session: Any | None,
        *,
        session_id: str | None,
    ) -> None:
        row_frame = ctk.CTkFrame(parent, fg_color="transparent")
        row_frame.pack(anchor="w", fill="x", padx=8, pady=4)
        font = ctk.CTkFont(size=15)
        muted = ("gray45", "gray55")

        ctk.CTkLabel(row_frame, text=display_name, font=font, anchor="w").pack(side="left")
        ctk.CTkLabel(row_frame, text=" · ", font=font, anchor="w", text_color=muted).pack(side="left")

        teammate_all = int(row_all["teammate_games"]) if row_all is not None else 0
        opponent_all = int(row_all["opponent_games"]) if row_all is not None else 0
        total_all = teammate_all + opponent_all

        if total_all == 0:
            ctk.CTkLabel(
                row_frame,
                text="no prior matches recorded",
                font=font,
                anchor="w",
                text_color="gray",
            ).pack(side="left")
            return

        ctk.CTkLabel(row_frame, text=f"{total_all} games", font=font, anchor="w").pack(side="left")

        if not session_id:
            return

        stg = int(row_session["teammate_games"]) if row_session is not None else 0
        sog = int(row_session["opponent_games"]) if row_session is not None else 0
        if stg + sog == 0:
            return

        if stg > 0:
            ctk.CTkLabel(row_frame, text=" · ", font=font, anchor="w", text_color=muted).pack(side="left")
            ctk.CTkLabel(row_frame, text="session teammate ", font=font, anchor="w").pack(side="left")
            stw = int(row_session["teammate_wins"])
            stl = int(row_session["teammate_losses"])
            ctk.CTkLabel(
                row_frame,
                text=f"{stw}-{stl}",
                font=font,
                anchor="w",
                text_color=_wl_record_color(stw, stl),
            ).pack(side="left")

        if sog > 0:
            ctk.CTkLabel(row_frame, text=" · ", font=font, anchor="w", text_color=muted).pack(side="left")
            ctk.CTkLabel(row_frame, text="session opponent ", font=font, anchor="w").pack(side="left")
            sow = int(row_session["opponent_wins"])
            sol = int(row_session["opponent_losses"])
            ctk.CTkLabel(
                row_frame,
                text=f"{sow}-{sol}",
                font=font,
                anchor="w",
                text_color=_wl_record_color(sow, sol),
            ).pack(side="left")

    def _refresh_lobby_encounters(self, snapshot: DashboardSnapshot) -> None:
        if self.lobby_encounters_frame is None:
            return

        current_sid = snapshot.session.session_id if snapshot.session else None
        if current_sid != self._lobby_session_id_for_encounters:
            self._lobby_session_id_for_encounters = current_sid
            self._lobby_encounter_cache_ids = None
            self._lobby_encounter_ui_signature = None

        others = [p for p in snapshot.live_players if not p.is_user and p.primary_id]
        if others:
            ids = tuple(sorted(p.primary_id for p in others))
            self._lobby_encounter_cache_ids = ids
        elif self._lobby_encounter_cache_ids is not None:
            ids = self._lobby_encounter_cache_ids
        else:
            ids = ()

        if not ids:
            signature = "placeholder"
            if signature == self._lobby_encounter_ui_signature:
                return
            self._lobby_encounter_ui_signature = signature
            for child in self.lobby_encounters_frame.winfo_children():
                child.destroy()
            ctk.CTkLabel(
                self.lobby_encounters_frame,
                text="Join a match to see how often you have played with each lobby player.",
                font=ctk.CTkFont(size=14),
                text_color="gray",
                wraplength=520,
                justify="left",
                anchor="w",
            ).pack(anchor="w", padx=8, pady=6)
            return

        db_rows = self.repository.get_encounters_for_primary_ids(ids)
        session_rows: dict[str, Any] = {}
        if current_sid:
            session_rows = self.repository.get_encounters_for_primary_ids_in_session(ids, current_sid)

        signature = _lobby_encounter_stats_signature(ids, db_rows, session_rows, current_sid or "")
        if signature == self._lobby_encounter_ui_signature:
            return

        self._lobby_encounter_ui_signature = signature
        for child in self.lobby_encounters_frame.winfo_children():
            child.destroy()

        if others:
            ordered = [
                (
                    p.primary_id,
                    (p.player_name or "").strip() or p.primary_id,
                )
                for p in sorted(others, key=lambda pl: (pl.player_name or "").lower())
            ]
        else:
            ordered = []
            for pid in sorted(ids):
                r = db_rows.get(pid)
                ordered.append((pid, str(r["player_name"]) if r is not None else pid))

        for pid, display_name in ordered:
            self._pack_lobby_player_row(
                self.lobby_encounters_frame,
                display_name,
                db_rows.get(pid),
                session_rows.get(pid),
                session_id=current_sid,
            )

    def _refresh_stats_tab(self, snapshot: DashboardSnapshot) -> None:
        if snapshot.session:
            self.record_label.configure(text=f"Session: {snapshot.session.wins} - {snapshot.session.losses}")
            self.mode_label.configure(text=f"Mode: {snapshot.session.game_mode}")
            session_id = snapshot.session.session_id
        else:
            self.record_label.configure(text="Session: 0 - 0")
            self.mode_label.configure(text=f"Mode: {snapshot.current_game_mode}")
            session_id = None

        inferred: str | None = None
        if snapshot.session:
            inferred = snapshot.session.game_mode
        elif snapshot.current_game_mode != "unknown":
            inferred = snapshot.current_game_mode
        if inferred in STANDARD_PLAYLIST_MODES and inferred != self.settings.comparison_game_mode:
            self.settings.comparison_game_mode = inferred
            self._comparison_mode_var.set(inferred)
            save_settings(self.settings)

        comparison_mode = _normalize_playlist_mode(self._comparison_mode_var.get())

        for stat_key, label in self.stat_live_personal_labels.items():
            user_val = snapshot.live_user_stats.get(stat_key)
            label.configure(text=_format_stat(stat_key, user_val), justify="right")

        for stat_key, label in self.stat_live_team_labels.items():
            inner = team_stat_suffix(stat_key)
            lines: list[str] = []
            for team in sorted(snapshot.live_teams, key=lambda t: t.team_num):
                team_label = team.team_name or f"Team {team.team_num}"
                tv = team.stats.get(inner)
                lines.append(f"{team_label}: {_format_stat(stat_key, tv)}")
            label.configure(
                text="\n".join(lines) if lines else "--",
                justify="right",
            )

        session_averages = self.repository.get_user_averages(
            game_mode=comparison_mode,
            session_id=session_id,
        )
        all_time_averages = self.repository.get_user_averages(game_mode=comparison_mode)
        global_baseline = self.repository.get_global_baseline(game_mode=comparison_mode)

        if self.stats_session_scope_label is not None:
            self.stats_session_scope_label.configure(
                text=_stats_comparison_scope_caption(comparison_mode, "session"),
            )
        if self.stats_global_scope_label is not None:
            self.stats_global_scope_label.configure(
                text=_stats_comparison_scope_caption(comparison_mode, "global"),
            )

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
        sort_label = (
            self.encounter_sort_combo.get()
            if hasattr(self, "encounter_sort_combo")
            else "Recent"
        )
        sort_by = "games" if sort_label == "Most games" else "recent"
        encounters = [
            row
            for row in self.repository.list_encounters(sort_by=sort_by)
            if search in str(row["player_name"]).lower()
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
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
            tw = int(encounter["teammate_wins"])
            tl = int(encounter["teammate_losses"])
            ow = int(encounter["opponent_wins"])
            ol = int(encounter["opponent_losses"])
            total = int(encounter["total_games"])
            tmg = int(encounter["teammate_games"])
            opg = int(encounter["opponent_games"])
            font = ctk.CTkFont(size=14)
            muted = ("gray40", "gray65")

            def add_lbl(text: str, *, tcolor: str | tuple[str, str] | None = None) -> None:
                kw: dict[str, Any] = {"font": font, "anchor": "w"}
                if tcolor is not None:
                    kw["text_color"] = tcolor
                ctk.CTkLabel(inner, text=text, **kw).pack(side="left")

            add_lbl(f"{encounter['player_name']}  |  {total} games")
            if tmg > 0:
                add_lbl("  |  ", tcolor=muted)
                add_lbl("Teammate ", tcolor=muted)
                add_lbl(f"{tw}-{tl}", tcolor=_wl_record_color(tw, tl))
            if opg > 0:
                add_lbl("  |  ", tcolor=muted)
                add_lbl("Opponent ", tcolor=muted)
                add_lbl(f"{ow}-{ol}", tcolor=_wl_record_color(ow, ol))
            self.encounter_rows.append(row)

    def _show_match_details(self, match_guid: str) -> None:
        detail = ctk.CTkToplevel(self)
        detail.title("Match Details")
        detail.geometry("960x640")
        detail.minsize(520, 400)
        outer = ctk.CTkScrollableFrame(detail, label_text=match_guid)
        outer.pack(fill="both", expand=True, padx=16, pady=16)
        outer.grid_columnconfigure(0, weight=1)
        players = self.repository.get_match_players(match_guid)
        for index, player in enumerate(players):
            card = ctk.CTkFrame(outer, fg_color=("gray85", "gray20"), corner_radius=10)
            card.grid(row=index, column=0, sticky="ew", padx=4, pady=10)
            card.grid_columnconfigure(0, weight=1)
            user_label = "User" if player["is_user"] else "Player"
            header = (
                f"{user_label}: {player['player_name']} ({player['primary_id']})\n"
                f"Team: {player['team_num']}"
            )
            ctk.CTkLabel(card, text=header, anchor="w", justify="left").grid(
                row=0, column=0, sticky="w", padx=12, pady=(10, 8)
            )
            stats_inner = ctk.CTkFrame(card, fg_color="transparent")
            stats_inner.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 12))
            stats_inner.grid_columnconfigure(1, weight=1)
            row_i = 0
            for key in SUPPORTED_STAT_KEYS:
                if key not in player.keys():
                    continue
                ctk.CTkLabel(stats_inner, text=STAT_LABELS[key], anchor="w").grid(
                    row=row_i, column=0, sticky="w", padx=8, pady=3
                )
                ctk.CTkLabel(
                    stats_inner,
                    text=_format_stat(key, player[key]),
                    anchor="e",
                    font=ctk.CTkFont(weight="bold"),
                ).grid(row=row_i, column=1, sticky="e", padx=8, pady=3)
                row_i += 1

    def _open_settings(self) -> None:
        modal = ctk.CTkToplevel(self)
        modal.title("Settings")
        modal.geometry("540x760")
        modal.grab_set()

        body = ctk.CTkFrame(modal, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(12, 0))

        tabview = ctk.CTkTabview(body)
        tabview.pack(fill="both", expand=True)

        dash_tab = tabview.add("Dashboard")
        glance_scroll = ctk.CTkScrollableFrame(dash_tab)
        glance_scroll.pack(fill="both", expand=True)
        glance_scroll.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            glance_scroll,
            text="Stats shown on the Dashboard during a match",
            font=ctk.CTkFont(size=13),
            text_color=("gray35", "gray70"),
            anchor="w",
        ).pack(anchor="w", padx=8, pady=(4, 10))
        glance_vars: dict[str, BooleanVar] = {}
        for stat in STAT_DEFINITIONS:
            if stat.key not in GLANCE_STAT_KEYS:
                continue
            variable = BooleanVar(value=stat.key in self.settings.glance_stats)
            checkbox = ctk.CTkCheckBox(glance_scroll, text=stat.label, variable=variable)
            checkbox.pack(anchor="w", padx=8, pady=3)
            glance_vars[stat.key] = variable

        stats_tab_settings = tabview.add("Stats columns")
        cols_scroll = ctk.CTkScrollableFrame(stats_tab_settings)
        cols_scroll.pack(fill="both", expand=True)
        cols_scroll.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            cols_scroll,
            text="Live Match — You column",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(anchor="w", padx=8, pady=(6, 4))
        ctk.CTkLabel(
            cols_scroll,
            text="Per-player stats (your car). Session vs all-time columns only use these.",
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray70"),
            anchor="w",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 8))

        variables: dict[str, BooleanVar] = {}
        for stat in STAT_DEFINITIONS:
            if not stat.supported:
                continue
            variable = BooleanVar(value=stat.key in self.settings.visible_stats)
            checkbox = ctk.CTkCheckBox(cols_scroll, text=stat.label, variable=variable)
            checkbox.pack(anchor="w", padx=8, pady=3)
            variables[stat.key] = variable

        ctk.CTkLabel(
            cols_scroll,
            text="Live Match — Teams column",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(anchor="w", padx=8, pady=(16, 4))
        ctk.CTkLabel(
            cols_scroll,
            text="Orange vs blue team totals. Not used in the comparison columns.",
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray70"),
            anchor="w",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 8))

        for stat in STAT_DEFINITIONS:
            if not stat.key.startswith("team_"):
                continue
            variable = BooleanVar(value=stat.key in self.settings.visible_stats)
            checkbox = ctk.CTkCheckBox(cols_scroll, text=stat.label, variable=variable)
            checkbox.pack(anchor="w", padx=8, pady=3)
            variables[stat.key] = variable

        integrations_tab = tabview.add("Ballchasing")
        int_scroll = ctk.CTkScrollableFrame(integrations_tab)
        int_scroll.pack(fill="both", expand=True)
        int_scroll.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            int_scroll,
            text=(
                "Create an API token on ballchasing.com (see link below), then paste it here. "
                "Your token is stored only in your local Demolytics settings file on this PC."
            ),
            wraplength=440,
            justify="left",
            anchor="w",
        ).pack(anchor="w", padx=8, pady=(8, 4))

        doc_link = ctk.CTkLabel(
            int_scroll,
            text="Open Ballchasing API documentation (authentication / token)",
            text_color=("#2563eb", "#60a5fa"),
            cursor="hand2",
            anchor="w",
        )
        doc_link.pack(anchor="w", padx=8, pady=(0, 12))
        doc_link.bind(
            "<Button-1>",
            lambda _e: webbrowser.open("https://ballchasing.com/doc/api"),
        )

        ballchasing_auto_var = BooleanVar(value=self.settings.ballchasing_auto_upload)
        ctk.CTkCheckBox(
            int_scroll,
            text="Automatically upload replays to ballchasing.com after online matches",
            variable=ballchasing_auto_var,
        ).pack(anchor="w", padx=8, pady=(0, 8))

        ctk.CTkLabel(int_scroll, text="API token", anchor="w").pack(anchor="w", padx=8, pady=(4, 2))
        token_entry = ctk.CTkEntry(
            int_scroll,
            width=420,
            show="*",
            placeholder_text="Paste token from ballchasing.com",
        )
        token_entry.pack(anchor="w", padx=8, pady=(0, 8))
        if self.settings.ballchasing_token:
            token_entry.insert(0, self.settings.ballchasing_token)

        ctk.CTkLabel(int_scroll, text="Replay visibility", anchor="w").pack(anchor="w", padx=8, pady=(4, 2))
        visibility_combo = ctk.CTkComboBox(
            int_scroll,
            values=list(BALLCHASING_VISIBILITY_CHOICES),
            width=200,
        )
        visibility_combo.set(self.settings.ballchasing_visibility)
        visibility_combo.pack(anchor="w", padx=8, pady=(0, 8))

        ctk.CTkLabel(
            int_scroll,
            text="When auto-upload is on, paste a token above or uploads will be skipped.",
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray70"),
            anchor="w",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", padx=8, pady=(8, 0))

        data_tab = tabview.add("Data")
        data_inner = ctk.CTkFrame(data_tab, fg_color="transparent")
        data_inner.pack(fill="both", expand=True, padx=8, pady=8)
        ctk.CTkLabel(
            data_inner,
            text=(
                "Reset saved performance numbers (per-match stats, session win/loss totals, and "
                "aggregates on the Stats tab) while keeping match history and Encounters "
                "(players you have seen and your record with or against each)."
            ),
            wraplength=440,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))
        ctk.CTkButton(
            data_inner,
            text="Reset performance statistics…",
            command=lambda: (modal.destroy(), self._confirm_reset_performance_statistics()),
        ).pack(anchor="w", pady=(0, 16))

        ctk.CTkLabel(
            data_inner,
            text=(
                "Deletes all saved matches, per-match player rows, sessions, and encounter counts "
                "from the local database. Your Settings file is not removed."
            ),
            wraplength=440,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(0, 12))
        ctk.CTkButton(
            data_inner,
            text="Reset all statistics…",
            fg_color="#8b3a3a",
            command=lambda: (modal.destroy(), self._confirm_reset_database()),
        ).pack(anchor="w")

        def save() -> None:
            self.settings.glance_stats = [
                key for key, variable in glance_vars.items() if variable.get() and key in GLANCE_STAT_KEYS
            ]
            if not self.settings.glance_stats:
                self.settings.glance_stats = list(DEFAULT_GLANCE_STATS)
            self.settings.visible_stats = [
                key for key, variable in variables.items() if variable.get() and key in STATS_TAB_COLUMN_KEYS
            ]
            if not self.settings.visible_stats:
                self.settings.visible_stats = list(DEFAULT_STATS_TAB_VISIBLE)
            self.settings.ballchasing_auto_upload = bool(ballchasing_auto_var.get())
            self.settings.ballchasing_token = token_entry.get().strip()
            vis = (visibility_combo.get() or "private").lower().strip()
            self.settings.ballchasing_visibility = (
                vis if vis in BALLCHASING_VISIBILITY_CHOICES else "private"
            )
            self.settings.settings_format_version = SETTINGS_FORMAT_VERSION
            save_settings(self.settings)
            modal.destroy()
            self._rebuild_glance_tab()
            self._rebuild_stats_tab()

        btn_row = ctk.CTkFrame(modal, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(8, 12))
        ctk.CTkButton(btn_row, text="Save", command=save).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Cancel", command=modal.destroy).pack(side="left")

    def _confirm_reset_performance_statistics(self) -> None:
        confirm = ctk.CTkToplevel(self)
        confirm.title("Reset performance statistics")
        confirm.geometry("440x260")
        confirm.grab_set()
        ctk.CTkLabel(
            confirm,
            text=(
                "This clears saved per-match performance stats and session win/loss totals. "
                "Match history and Encounters (your record with or against each player) are kept. "
                "This cannot be undone."
            ),
            wraplength=400,
            justify="left",
        ).pack(padx=20, pady=20)

        actions = ctk.CTkFrame(confirm, fg_color="transparent")
        actions.pack(pady=(0, 16))

        def do_reset() -> None:
            self.repository.clear_performance_statistics_preserving_matches()
            self.aggregator.reset_session_and_frozen_stats_after_db_performance_clear()
            self.snapshot = self.aggregator.snapshot()
            if self.snapshot.session:
                self.repository.upsert_session(self.snapshot.session)
            confirm.destroy()
            self._refresh_all_views()
            messagebox.showinfo(
                "Demolytics",
                "Performance statistics were reset. Encounters and match history were kept.",
            )

        ctk.CTkButton(actions, text="Cancel", width=100, command=confirm.destroy).pack(side="left", padx=8)
        ctk.CTkButton(
            actions,
            text="Reset stats",
            width=120,
            command=do_reset,
        ).pack(side="left", padx=8)

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
        self.stat_live_personal_labels.clear()
        self.stat_live_team_labels.clear()
        self.session_average_labels.clear()
        self.global_average_labels.clear()
        self.stats_session_scope_label = None
        self.stats_global_scope_label = None
        self._comparison_mode_var.set(_normalize_playlist_mode(self.settings.comparison_game_mode))
        self._build_stats_tab()
        self._refresh_stats_tab(self.snapshot)

    def _clear_root(self) -> None:
        if self.api_thread is not None:
            self.api_thread.stop()
            self.api_thread = None
        for child in self.winfo_children():
            child.destroy()

    def _on_close(self) -> None:
        if self._snackbar_after_id is not None:
            try:
                self.after_cancel(self._snackbar_after_id)
            except Exception:
                pass
            self._snackbar_after_id = None
        if self.api_thread is not None:
            self.api_thread.stop()
        self.destroy()


def _stats_comparison_scope_caption(
    comparison_mode: str,
    column: str,
) -> str:
    """Explains session / baseline columns use the playlist selected above (or auto from lobby)."""
    mode = _normalize_playlist_mode(comparison_mode)
    if column == "session":
        return (
            f"{mode}: this session's per-game average vs your all-time per-game average in {mode} "
            f"(playlist chosen above; lobby may switch it automatically)."
        )
    return (
        f"{mode}: your all-time per-game average vs the average for other players recorded in your "
        f"{mode} matches (everyone except you in those games)."
    )


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

