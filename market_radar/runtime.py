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
from .provider import MarketDataProvider, ProviderError, RateLimitError
from .storage import RadarStore


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

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return
        self.stop_event.clear()
        self.error = None
        self.thread = threading.Thread(target=self._run, name="market-radar", daemon=False)
        self.thread.start()

    def stop(self):
        self.stop_event.set()  # UI never waits for a network request or export.
        if hasattr(self.provider, "request_stop"):
            self.provider.request_stop()

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
        if hasattr(self.provider, "set_logger"):
            self.provider.set_logger(logger)
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
                identity.update(schema_version=2, rvol_basis="completed_1m", bar_label_policy=self.config.bar_label_policy,
                    exact_vwap_enabled=False, include_extended_hours=False,
                    bar_finalization_seconds=self.config.bar_finalization_seconds,
                    max_ws_symbols=self.config.max_ws_symbols, candidate_pages=self.config.candidate_pages,
                    stock_type_policy=self.config.stock_type_policy)
            store = RadarStore(self.config.db_path, identity)
            engine = RadarEngine(store, self.config, self.provider.name, self.provider.mode)
            next_export = time.monotonic() + self.config.export_seconds
            while not self.stop_event.is_set():
                started = time.monotonic()
                wait = self.config.snapshot_seconds
                try:
                    batch = self.provider.fetch(store.tracked_tickers())
                    now = datetime.now(timezone.utc)
                    delay = (now - instant(batch.timestamp_utc)).total_seconds()
                    if self.provider.mode == "live" and (delay > self.config.max_data_delay_seconds or delay < -5):
                        logger.warning("data_delay seconds=%.1f batch=%s", delay, batch.timestamp_utc)
                        self.publish(f"데이터 지연/시각 오류 ({delay:.0f}s): 갱신 보류, 기존 표는 과거 데이터")
                    else:
                        rows = engine.process(batch, utc(now))
                        self.publish(f"{self.provider.name} | {batch.universe} | ET {instant(batch.timestamp_utc).astimezone(ET).isoformat()}", rows)
                    if time.monotonic() >= next_export:
                        folders = store.export_all(self.config.data_dir / "export")
                        logger.info("export_success days=%s", len(folders))
                        next_export = time.monotonic() + self.config.export_seconds
                except StopIteration:
                    final_status = "REPLAY 완료 · 화면은 마지막 기록 데이터" if self.provider.mode == "replay" else "공급원 데이터 종료"
                    break
                except RateLimitError as exc:
                    wait = max(wait, exc.retry_after_seconds)
                    logger.warning("rate_limit retry_seconds=%s", wait)
                    self.publish(f"Rate limit: {wait:.0f}초 후 재시도 · 기존 표는 과거 데이터")
                except ProviderError:
                    logger.exception("api_error")
                    self.publish("API 오류 · 재시도 대기 · 기존 표는 과거 데이터")
                except sqlite3.Error:
                    self.failed_batches += 1
                    logger.exception("db_error")
                    self.publish("DB 오류 · 저장 실패 · 기존 표는 과거 데이터")
                except (ValueError, TypeError, KeyError, ArithmeticError):
                    self.failed_batches += 1
                    logger.exception("calculation_exception")
                    self.publish("데이터 검증/계산 오류 · 해당 batch 제외")
                except OSError:
                    logger.exception("export_or_io_error")
                    self.error = "CSV export/IO 오류: runtime 로그 확인 필요"
                    self.publish(self.error)
                if self.fast_replay:
                    continue
                elapsed = time.monotonic() - started
                if elapsed > self.config.snapshot_seconds:
                    logger.warning("data_delay cycle_seconds=%.1f", elapsed)
                # Rate limit delay begins after response; regular polling uses a fixed cadence.
                self.stop_event.wait(max(0, wait if wait > self.config.snapshot_seconds else wait - elapsed))
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            final_status = self.error
            logger.exception("db_error" if isinstance(exc, sqlite3.Error) else "api_or_startup_error")
        finally:
            if store:
                try:
                    folders = store.export_all(self.config.data_dir / "export")
                    logger.info("export_success final days=%s", len(folders))
                except Exception:
                    logger.exception("export_error")
                    self.error = final_status = "CSV export 실패: runtime 로그 확인 필요"
                finally:
                    store.close()
            try:
                self.provider.close()
            except Exception:
                logger.exception("provider_disconnect_error")
            logger.info("provider_disconnected connected=%s", connected)
            logger.info("program_stop")
            if self.failed_batches:
                self.error = f"{self.failed_batches}개 batch 검증/DB 저장 실패: runtime 로그 확인 필요"
                final_status = self.error
            self.publish(final_status)
            handler.close()
