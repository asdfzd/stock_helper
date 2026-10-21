"""XNYS session gate. All timestamps describe observations, never backdated starts."""
from datetime import datetime, timedelta, timezone
import threading
import time

from .models import ET, instant, utc

MODES = ("Manual", "Regular Session", "Capacity Test")
CAPACITY_MINUTES = 65
START_STOP_TOLERANCE_MS = 1000
LATE_CAPACITY = ("Today's standardized capacity window has already started. "
                 "Use Manual mode or wait for next trading session.")


class SystemClock:
    def now(self):
        return datetime.now(timezone.utc)

    def monotonic(self):
        return time.monotonic()


class SessionSchedule:
    def __init__(self, config, calendar=None, clock=None):
        self.config, self.clock = config, clock or SystemClock()
        self.lock = threading.RLock()
        self.phase = "ARMED" if config.collection_mode != "Manual" else "PREPARING"
        self.reasons = []
        now = self.clock.now()
        self.last_wall, self.last_mono = now, self.clock.monotonic()
        self.started_mono = None  # Observed gate opening; no reconstructed timestamp.
        self.open = self.close = self.stop_at = self.prepare_at = None
        self.data = dict(collection_mode=config.collection_mode, mode=config.collection_mode,
            protocol_name={"Manual": "manual_diagnostic", "Capacity Test": "capacity", "Regular Session": "weekly_regular"}[config.collection_mode],
            protocol_version=1, protocol_compliant=False, protocol_reason="in_progress",
            armed_at=utc(now), prepare_started_at=None, scheduled_open_et=None, scheduled_open_utc=None,
            scheduled_close_et=None, scheduled_close_utc=None, scheduled_start=None, scheduled_stop=None,
            actual_regular_start_utc=None, actual_start=None, actual_stop=None,
            ready_before_open=False, connection_ready_at=None,
            start_delay_ms=None, stop_delay_ms=None, armed_before_open=False, late_start=False,
            stop_reason=None, session_date_et=now.astimezone(ET).date().isoformat(),
            max_ws_symbols=config.max_ws_symbols, snapshot_seconds=config.snapshot_seconds,
            result_pre_target_grace_seconds=config.result_pre_target_grace_seconds,
            shared_cache_path=str(config.cache_path), first_regular_batch_utc=None,
            full_session=False, regular_duration_seconds=None,
            start_stop_tolerance_ms=START_STOP_TOLERANCE_MS)
        if config.collection_mode == "Manual":
            self.reasons.append("manual_diagnostic")
            self.data["prepare_started_at"] = utc(now)
            return
        if calendar is None:
            from .providers.kiwoom_calendar import ExchangeCalendar
            calendar = ExchangeCalendar()
        day = now.astimezone(ET).date()
        sessions = calendar.sessions(day.isoformat(), (day + timedelta(days=30)).isoformat())
        session = next((s for s in sessions if instant(s.close_utc) > now), None)
        if session is None:
            raise ValueError("No upcoming XNYS session in calendar coverage")
        self.open, self.close = instant(session.open_utc), instant(session.close_utc)
        if config.collection_mode == "Capacity Test" and now >= self.open:
            raise ValueError(LATE_CAPACITY)
        self.stop_at = (self.open + timedelta(minutes=CAPACITY_MINUTES)
                        if config.collection_mode == "Capacity Test" else self.close)
        self.prepare_at = self.open - timedelta(minutes=config.preopen_prepare_minutes)
        self.data.update(session_date_et=session.date, calendar_source=calendar.source,
            scheduled_open_et=self.open.astimezone(ET).isoformat(), scheduled_open_utc=utc(self.open),
            scheduled_close_et=self.close.astimezone(ET).isoformat(), scheduled_close_utc=utc(self.close),
            scheduled_start=utc(self.open), scheduled_stop=utc(self.stop_at),
            armed_before_open=now < self.open, late_start=now >= self.open)
        if now >= self.open:
            self.invalidate("late_arm")

    def invalidate(self, reason):
        with self.lock:
            if reason not in self.reasons:
                self.reasons.append(reason)
            self.data.update(protocol_compliant=False, protocol_reason=";".join(self.reasons))

    def snapshot(self):
        with self.lock:
            return dict(self.data)

    def tick(self, ready=False):
        with self.lock:
            now, mono = self.clock.now(), self.clock.monotonic()
            wall_delta = (now - self.last_wall).total_seconds()
            mono_delta = mono - self.last_mono
            if self.open and self.phase not in {"STOPPED", "ERROR", "FINALIZING"}:
                if abs(wall_delta - mono_delta) > 2 or wall_delta < 0:
                    self.invalidate("clock_jump")
                if max(wall_delta, mono_delta) > 5 and now >= self.open:
                    self.invalidate("scheduler_gap_or_resume")
            self.last_wall, self.last_mono = now, mono
            if self.phase in {"FINALIZING", "STOPPED", "ERROR"}:
                return None
            if self.stop_at and now >= self.stop_at:
                self.stop("scheduled_capacity_end" if self.config.collection_mode == "Capacity Test" else "scheduled_close")
                return "stop"
            if self.phase == "ARMED" and now >= self.prepare_at:
                self.phase = "PREPARING"
                self.data["prepare_started_at"] = utc(now)
                return "prepare"
            if ready and self.phase in {"PREPARING", "PREOPEN_WAIT"}:
                if self.data["connection_ready_at"] is None:
                    self.data["connection_ready_at"] = utc(now)
                if self.open and now < self.open:
                    self.data["ready_before_open"] = True
                    self.phase = "PREOPEN_WAIT"
                else:
                    self.phase = "REGULAR_LIVE"
                    self.started_mono = mono
                    delay = (now - self.open).total_seconds() * 1000 if self.open else None
                    self.data.update(actual_start=utc(now), actual_regular_start_utc=utc(now), start_delay_ms=delay)
                    if self.open and not self.data["ready_before_open"]:
                        self.invalidate("connection_not_ready_before_open")
                    if delay is not None and delay > START_STOP_TOLERANCE_MS:
                        self.data["late_start"] = True
                        self.invalidate("delayed_start")
            return None

    def allows_research(self, timestamp=None):
        if self.config.collection_mode == "Manual":
            return True
        with self.lock:
            now = self.clock.now()
            stamp = instant(timestamp) if timestamp else now
            return (self.phase == "REGULAR_LIVE" and self.open <= now < self.stop_at
                    and self.open <= stamp < self.stop_at)

    def stop(self, reason):
        with self.lock:
            if self.data["stop_reason"] is not None:
                return
            now = self.clock.now()
            delay = (now - self.stop_at).total_seconds() * 1000 if self.stop_at else None
            self.phase = "FINALIZING"
            self.data.update(actual_stop=utc(now), stop_delay_ms=delay, stop_reason=reason)
            if reason not in {"scheduled_capacity_end", "scheduled_close"}:
                self.invalidate(reason)
            if delay is not None and abs(delay) > START_STOP_TOLERANCE_MS:
                self.invalidate("stop_time_mismatch")
            if self.data["actual_start"] is None:
                self.invalidate("never_started")

    def finish(self, success):
        with self.lock:
            if not success:
                self.invalidate("collection_or_finalization_error")
            self.phase = "STOPPED" if success else "ERROR"
            self.data.update(protocol_compliant=success and not self.reasons,
                protocol_reason=";".join(self.reasons) or "completed_protocol",
                full_session=success and not self.reasons and self.config.collection_mode == "Regular Session",
                regular_duration_seconds=(instant(self.data["actual_stop"]) - instant(self.data["actual_start"])).total_seconds()
                    if self.data["actual_start"] and self.data["actual_stop"] else None)


def validate_protocol_config(config):
    """Only max_ws is an experimental policy variable; paths/lead time are operational."""
    if config.collection_mode == "Manual":
        return
    from .config import CONFIG
    excluded = {"max_ws_symbols", "data_dir", "historical_cache_path", "collection_mode", "preopen_prepare_minutes"}
    from dataclasses import fields
    changed = [f.name for f in fields(config) if f.name not in excluded
               and getattr(config, f.name) != getattr(CONFIG, f.name)]
    if changed:
        raise ValueError("Scheduled protocol requires Collector v2.1 default policies: " + ", ".join(changed))
    if config.cache_path.resolve().is_relative_to(config.data_dir.resolve()):
        raise ValueError("Scheduled historical cache must be outside the session directory")
