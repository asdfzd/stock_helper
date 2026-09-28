"""Single market-only WebSocket session with acknowledged subscription state."""
from datetime import datetime, timezone
import json
import logging
import threading
import time

URL = "wss://api.kiwoom.com:10000/api/us/websocket"
FE_FIELDS = set("10 12 13 14 15 16 17 18 20 22 290 51020".split())
FT_FIELDS = {str(i) for i in range(41, 81)} | {"21"}


class KiwoomWebSocket:
    def __init__(self, rest, config, stop, raw_sink, logger, connector=None):
        self.rest, self.config, self.stop = rest, config, stop
        self.raw_sink, self.logger, self.connector = raw_sink, logger, connector
        self.lock = threading.RLock()
        self.desired, self.active, self.latest = {}, {}, {}
        self.connected = False
        self.gap = False
        self.generation = 0
        self.counts = {"FE": 0, "FT": 0, "PING": 0}
        self.thread = None
        self.socket = None

    def set_symbols(self, symbols):
        if len(symbols) > self.config.max_ws_symbols or len(symbols) > 200:
            raise ValueError("WebSocket symbol limit exceeded")
        with self.lock:
            self.desired = dict(symbols)

    def snapshot(self):
        with self.lock:
            return {k: dict(v) for k, v in self.latest.items()}, dict(self.active), self.gap, self.connected

    def handle(self, message, send):
        if message.get("trnm") == "PING":
            send(json.dumps(message))
            self.counts["PING"] += 1
        elif message.get("trnm") == "REAL":
            at = datetime.now(timezone.utc).isoformat()
            for item in message.get("data", []):
                kind, ticker = item.get("type"), item.get("item")
                if kind not in ("FE", "FT") or not isinstance(ticker, str):
                    continue
                if kind == "FT" and not self.config.enable_orderbook_capture:
                    continue
                fields = FE_FIELDS if kind == "FE" else FT_FIELDS
                values = {k: v for k, v in item.get("values", {}).items() if k in fields and isinstance(v, (str, int, float))}
                row = {"values": values, "received_utc": at, "exchange": item.get("stexTp"),
                       "connection_generation": self.generation, "provider_mode": "real"}
                with self.lock:
                    self.counts[kind] += 1
                    if kind == "FE":
                        self.latest[ticker] = row
                self.raw_sink(kind, ticker, at, row)

    def packet(self, name, symbols):
        packet = {"trnm": name, "grp_no": "1", "data": [{"item": [
            {"jmcode": ticker, "stex_tp": exchange} for ticker, exchange in symbols.items()],
            "type": ["FE", "FT"] if self.config.enable_orderbook_capture else ["FE"]}]}
        if name == "REG":
            packet["refresh"] = "1"  # official FE schema: keep previously registered item/type
        return packet

    def _ack(self, ws, name, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = json.loads(ws.recv(timeout=max(.01, deadline-time.monotonic())))
            self.handle(msg, ws.send)
            if msg.get("trnm") == name:
                if str(msg.get("return_code")) != "0":
                    raise RuntimeError("Kiwoom WS acknowledgement rejected")
                return
        raise TimeoutError("WS acknowledgement timeout")

    def sync_symbols(self, ws):
        with self.lock:
            desired, active = dict(self.desired), dict(self.active)
        removed = {k: v for k, v in active.items() if desired.get(k) != v}
        added = {k: v for k, v in desired.items() if active.get(k) != v}
        for name, group in (("REMOVE", removed), ("REG", added)):
            if not group:
                continue
            ws.send(json.dumps(self.packet(name, group)))
            self._ack(ws, name)
            with self.lock:
                if name == "REMOVE":
                    for ticker in group:
                        self.active.pop(ticker, None)
                        self.latest.pop(ticker, None)
                else:
                    self.active.update(group)
            self.logger.info("kiwoom_subscription_%s count=%s", "add" if name == "REG" else "remove", len(group))

    def start(self):
        self.thread = threading.Thread(target=self._run, name="kiwoom-ws", daemon=False)
        self.thread.start()

    def _run(self):
        from websockets.sync.client import connect
        connector = self.connector or connect
        logging.getLogger("websockets").setLevel(logging.CRITICAL)
        attempts = 0
        while not self.stop.is_set():
            try:
                token = self.rest.token(force=attempts > 0)
                with connector(URL, proxy=None, open_timeout=10, close_timeout=3,
                               ping_interval=None, max_size=4*1024*1024) as ws:
                    self.socket = ws
                    ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
                    self._ack(ws, "LOGIN")
                    with self.lock:
                        self.generation += 1
                        self.connected = True
                        self.active.clear()
                        self.latest.clear()  # never reuse prices from a disconnected socket
                    self.logger.info("kiwoom_ws_reconnected" if self.generation > 1 else "kiwoom_ws_connected")
                    connected_at = time.monotonic()
                    while not self.stop.is_set():
                        self.sync_symbols(ws)
                        try:
                            self.handle(json.loads(ws.recv(timeout=.5)), ws.send)
                        except TimeoutError:
                            pass
                        if time.monotonic()-connected_at > 30:
                            attempts = 0
                    if self.active:
                        ws.send(json.dumps(self.packet("REMOVE", self.active)))
                        self._ack(ws, "REMOVE", timeout=3)
                        self.logger.info("kiwoom_subscription_remove count=%s", len(self.active))
            except Exception:
                if not self.stop.is_set():
                    with self.lock:
                        self.gap = True
                    self.logger.warning("kiwoom_data_gap websocket_disconnect")
                    self.raw_sink("GAP", None, datetime.now(timezone.utc).isoformat(), {"reason": "ws_disconnect"})
                    attempts += 1
            finally:
                with self.lock:
                    self.connected = False
                    self.active.clear()
                    self.latest.clear()
                self.socket = None
                self.logger.info("kiwoom_ws_disconnected")
            if self.stop.wait(min(self.config.reconnect_max_seconds, 2 ** min(attempts, 6))):
                break

    def close(self):
        if self.thread:
            self.thread.join(timeout=25)
            if self.thread.is_alive() and self.socket:
                self.socket.close()
                self.thread.join(timeout=15)
            if self.thread.is_alive():
                raise RuntimeError("Kiwoom WS shutdown timeout")
