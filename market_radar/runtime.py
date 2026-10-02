from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone

from .config import RadarConfig
from .engine import RadarEngine
from .models import ET, instant, utc
from .provider import MarketDataProvider, ProviderError, RateLimitError, UniverseWarmingUp, UniverseStale
from .quality import QualityCounters, QualityLogHandler, event
from .storage import RadarStore
from .providers.kiwoom_failures import StopRequested


class DailyLogHandler(logging.Handler):
    def __init__(self, directory):
        super().__init__()
        self.directory = directory
        self.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)s %(message)s"))
        self.formatter.converter = time.gmtime

    def emit(self, record):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            day = datetime.now(ET).date().isoformat()
            with (self.directory / f"{day}.log").open("a", encoding="utf-8") as stream:
                stream.write(self.format(record) + "\n")
        except OSError:
            self.handleError(record)


class RadarWorker:
    """All provider IO, calculations, SQLite and CSV operations stay off Qt."""
    def __init__(self, provider: MarketDataProvider, config: RadarConfig, *, fast_replay=False):
        if provider.mode not in {"live", "replay"}:
            raise ValueError("Provider must identify live/replay mode")
        self.provider = provider
        self.config = replace(config, data_dir=config.data_dir / "replay") if provider.mode == "replay" else config
        if provider.mode == "replay" and self.config.provider == "kiwoom":
            self.config = replace(self.config, provider="unconfigured", test_version="market_radar_v1")
        self.fast_replay = fast_replay and provider.mode == "replay"
        self.messages = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.thread = None
        self.error = None
        self.failed_batches = 0
        self._last_rows = None
        self.logger = None
        self.shutdown_started = None
        self.shutdown_requested_utc = None
        self.shutdown_elapsed_ms = None
        self.phase = "STOPPED"

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return
        self.stop_event.clear()
        self.error = None
        self.phase = "STARTING"
        self.thread = threading.Thread(target=self._run, name="market-radar", daemon=False)
        self.thread.start()

    def stop(self, reason='stop'):
        first = not self.stop_event.is_set()
        if first:
            self.shutdown_started = time.monotonic()
            self.shutdown_requested_utc = utc(datetime.now(timezone.utc))
        if self.logger:
            self.logger.info('shutdown_requested reason=%s timestamp=%s elapsed_ms=%.3f', reason,
                utc(datetime.now(timezone.utc)), self._shutdown_elapsed(), extra=event('shutdown_requested_count'))
        self.stop_event.set()  # UI never waits for a network request or export.
        self.phase = "STOPPING"
        if hasattr(self.provider, "request_stop"):
            self.provider.request_stop()
        if first and self.logger:
            self.logger.info('shutdown_stop_scheduling elapsed_ms=%.3f', self._shutdown_elapsed())

    def _shutdown_elapsed(self):
        return 1000*(time.monotonic()-self.shutdown_started) if self.shutdown_started is not None else 0

    def publish(self, status, rows=None):
        if rows is not None:
            self._last_rows = rows
        message = {"status": status, "rows": self._last_rows, "mode": self.provider.mode}
        try:
            self.messages.put_nowait(message)
        except queue.Full:
            try:
                self.messages.get_nowait()
            except queue.Empty:
                pass
            self.messages.put_nowait(message)

    def _run(self):
        logger = logging.Logger(f"market_radar.{id(self)}", logging.INFO)
        handler = DailyLogHandler(self.config.data_dir / "logs")
        logger.addHandler(handler)
        counters = QualityCounters()
        counters.add("collector_start_count")
        logger.addHandler(QualityLogHandler(counters))
        if hasattr(self.provider, "set_logger"):
            self.provider.set_logger(logger)
        self.logger = logger
        store = None
        connected = False
        final_status = "수집 중지"
        logger.info("program_start provider=%s mode=%s version=%s", self.provider.name, self.provider.mode, self.config.test_version)
        try:
            self.publish(f"{self.provider.name}: 연결 중")
            self.provider.connect()
            connected = True
            logger.info("provider_connected %s", self.provider.name)
            identity = {
                "provider": self.provider.name, "mode": self.provider.mode,
                "test_version": self.config.test_version, "session": self.config.session,
                "rvol_lookback_days": self.config.rvol_lookback_days,
                "snapshot_seconds": self.config.snapshot_seconds,
                "result_tolerance_seconds": self.config.result_tolerance_seconds,
            }
            if self.provider.name == "kiwoom":
                identity.update(schema_version=4 if self.config.test_version == "kiwoom_radar_v2_1" else 3, rvol_basis="completed_1m", bar_label_policy=self.config.bar_label_policy,
                    exact_vwap_enabled=False, include_extended_hours=False,
                    bar_finalization_seconds=self.config.bar_finalization_seconds,
                    max_ws_symbols=self.config.max_ws_symbols, candidate_pages=self.config.candidate_pages,
                    stock_type_policy=self.config.stock_type_policy,
                    rvol_backfill_enabled=self.config.rvol_backfill_enabled,
                    rvol_backfill_min_day_volume=self.config.rvol_backfill_min_day_volume,
                    rvol_backfill_min_price=self.config.rvol_backfill_min_price,
                    result_tracking_hold_minutes=self.config.result_tracking_hold_minutes)
            if self.provider.name == 'kiwoom' and self.config.test_version == 'kiwoom_radar_v2_1':
                identity.update(result_pre_target_grace_seconds=self.config.result_pre_target_grace_seconds,
                                rest_negative_ttl_seconds=self.config.rest_negative_ttl_seconds)
            store = RadarStore(self.config.db_path, identity)
            engine = RadarEngine(store, self.config, self.provider.name, self.provider.mode)
            self.phase = "RUNNING"
            next_export = time.monotonic() + self.config.export_seconds
            while not self.stop_event.is_set():
                started = time.monotonic()
                wait = self.config.snapshot_seconds
                try:
                    if hasattr(self.provider, "set_result_tracking"):
                        self.provider.set_result_tracking(store.protected_tracking(utc(datetime.now(timezone.utc))))
                    batch = self.provider.fetch(store.tracked_tickers())
                    now = datetime.now(timezone.utc)
                    delay = (now - instant(batch.timestamp_utc)).total_seconds()
                    if self.provider.mode == "live" and (delay > self.config.max_data_delay_seconds or delay < -5):
                        logger.warning("data_delay seconds=%.1f batch=%s", delay, batch.timestamp_utc)
                        self.publish(f"데이터 지연/시각 오류 ({delay:.0f}s): 갱신 보류, 기존 표는 과거 데이터")
                    else:
                        rows = engine.process(batch, utc(now))
                        if hasattr(self.provider, "set_result_tracking"):
                            self.provider.set_result_tracking(store.protected_tracking(utc(now)))
                        self.publish(f"{self.provider.name} | {batch.universe} | ET {instant(batch.timestamp_utc).astimezone(ET).isoformat()}", rows)
                    counters.persist(store)
                    if time.monotonic() >= next_export:
                        folders = store.export_all(self.config.data_dir / "export")
                        logger.info("export_success days=%s", len(folders))
                        next_export = time.monotonic() + self.config.export_seconds
                except StopIteration:
                    final_status = "REPLAY 완료 · 화면은 마지막 기록 데이터" if self.provider.mode == "replay" else "공급원 데이터 종료"
                    break
                except UniverseWarmingUp:
                    logger.info("kiwoom_universe_warming_up")
                    self.publish("Kiwoom universe 준비 중")
                except UniverseStale:
                    logger.warning("kiwoom_universe_stale", extra=event("stale_count"))
                    self.publish("Kiwoom universe 갱신 지연 · 기존 표는 과거 데이터")
                except RateLimitError as exc:
                    wait = max(wait, exc.retry_after_seconds)
                    logger.warning("rate_limit retry_seconds=%s", wait)
                    self.publish(f"Rate limit: {wait:.0f}초 후 재시도 · 기존 표는 과거 데이터")
                except StopRequested:
                    break
                except ProviderError:
                    logger.exception("api_error", extra=event("api_error_count"))
                    self.publish("API 오류 · 재시도 대기 · 기존 표는 과거 데이터")
                except sqlite3.Error:
                    self.failed_batches += 1
                    logger.exception("db_error", extra=event("db_error_count"))
                    self.publish("DB 오류 · 저장 실패 · 기존 표는 과거 데이터")
                except (ValueError, TypeError, KeyError, ArithmeticError):
                    self.failed_batches += 1
                    logger.exception("calculation_exception", extra=event("worker_error_count"))
                    self.publish("데이터 검증/계산 오류 · 해당 batch 제외")
                except OSError:
                    logger.exception("export_or_io_error", extra=event("export_error_count"))
                    self.error = "CSV export/IO 오류: runtime 로그 확인 필요"
                    self.publish(self.error)
                if self.fast_replay:
                    continue
                elapsed = time.monotonic() - started
                if elapsed > self.config.snapshot_seconds:
                    logger.warning("data_delay cycle_seconds=%.1f", elapsed)
                # Rate limit delay begins after response; regular polling uses a fixed cadence.
                self.stop_event.wait(max(0, wait if wait > self.config.snapshot_seconds else wait - elapsed))
        except StopRequested:
            pass
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            final_status = self.error
            logger.exception("db_error" if isinstance(exc, sqlite3.Error) else "api_or_startup_error",
                             extra=event("db_error_count" if isinstance(exc, sqlite3.Error) else "worker_error_count"))
        finally:
            if not self.stop_event.is_set():
                self.stop(reason="collection_complete")
            try:
                self.provider.close()
            except Exception:
                logger.exception("provider_disconnect_error", extra=event("worker_error_count"))
                self.error = final_status = "Provider 종료 실패: runtime 로그 확인 필요"
            if store:
                try:
                    logger.info('shutdown_export_start elapsed_ms=%.3f', self._shutdown_elapsed())
                    store.db.commit()
                    store.db.execute('PRAGMA wal_checkpoint(PASSIVE)')
                    counters.persist(store)
                    folders = store.export_all(self.config.data_dir / "export")
                    logger.info("export_success final days=%s", len(folders))
                    logger.info('shutdown_export_complete elapsed_ms=%.3f', self._shutdown_elapsed())
                    self.shutdown_elapsed_ms = self._shutdown_elapsed()
                    counters.set_gauge('shutdown_elapsed_ms', self.shutdown_elapsed_ms)
                    counters.persist(store)
                    # Refresh only the small summary after timing the CSV export.
                    from .quality import daily_summary
                    import json, os
                    for folder in folders if store.schema_version >= 4 else ():
                        temporary = folder/'quality_summary.json.tmp'
                        temporary.write_text(json.dumps(daily_summary(store, folder.name), ensure_ascii=False, indent=2), encoding='utf-8')
                        os.replace(temporary, folder/'quality_summary.json')
                except Exception:
                    logger.exception("export_error", extra=event("export_error_count"))
                    try:
                        counters.persist(store)
                    except sqlite3.Error:
                        logger.error("db_error saving final counters")
                    self.error = final_status = "CSV export 실패: runtime 로그 확인 필요"
                finally:
                    store.close()
            logger.info("provider_disconnected connected=%s", connected)
            logger.info("shutdown_complete elapsed_ms=%.3f", self._shutdown_elapsed())
            logger.info("program_stop")
            if self.failed_batches:
                self.error = f"{self.failed_batches}개 batch 검증/DB 저장 실패: runtime 로그 확인 필요"
                final_status = self.error
            self.publish(final_status)
            self.phase = "ERROR" if self.error else "STOPPED"
            handler.close()
