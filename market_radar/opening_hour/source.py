"""Read finalized evidence via temporary DB/WAL copies; never open originals with SQLite."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

from ..evidence_io import copied_database
from ..config import CONFIG
from ..models import ET, instant, utc
from ..providers.kiwoom_calendar import ExchangeCalendar
from ..providers.kiwoom_cache import POLICY, number


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def logical_evidence(evidence):
    """Paths locate input files; they are never part of a research dataset identity."""
    return {k: v for k, v in evidence.items() if k not in {
        "source_db_path", "source_cache_path", "source_paper_paths", "source_fingerprint", "_operational"}}


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def unpack(value, default=None):
    return json.loads(value) if isinstance(value, str) else value if value is not None else default


def _inside(at, start, end):
    return bool(at) and start <= instant(at) < end


def read_session(session_dir, *, day=None, cache_path=None, paper_paths=(), calendar=None, diagnostics=None):
    root = Path(session_dir).resolve(strict=True)
    lifecycle_path = root/"dashboard_run.json"
    lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8-sig")) if lifecycle_path.exists() else {}
    if lifecycle and not lifecycle.get("stopped_at"):
        raise ValueError("Analyze a stopped session only")
    source_paths = sorted(root.glob("stock_helper_market*.db"))
    if len(source_paths) != 1:
        raise ValueError("Expected one session research DB")
    source = source_paths[0]
    with copied_database(source, diagnostics=diagnostics, phase='radar') as db:
        names = tables(db)
        if not {"radar_metadata", "radar_batches", "radar_snapshots", "market_sessions"} <= names:
            raise ValueError("Unsupported Radar source")
        metadata = dict(db.execute("SELECT key,value FROM radar_metadata"))
        identity = unpack(metadata.get("identity"), {})
        protocol = unpack(metadata.get("collection_protocol"), lifecycle.get("collection_protocol", {}))
        dates = [r[0] for r in db.execute("SELECT session_date FROM market_sessions ORDER BY session_date")]
        day = day or protocol.get("session_date_et") or (dates[0] if len(dates) == 1 else None)
        if day is None:
            raise ValueError("Select --date for a multi-day or undated session")
        calendar = calendar or ExchangeCalendar()
        session = calendar.session(day)
        if session is None:
            raise ValueError("Not an XNYS trading date")
        session.validate()
        start, end = instant(session.open_utc), instant(session.open_utc)+timedelta(minutes=60)
        saved = db.execute("SELECT open_utc,close_utc FROM market_sessions WHERE session_date=?", (day,)).fetchone()
        if not saved or instant(saved[0]) != start or instant(saved[1]) != instant(session.close_utc):
            raise ValueError("Source session disagrees with XNYS calendar")
        if end > instant(session.close_utc):
            raise ValueError("Official session shorter than the requested hour")
        snapshots = [dict(r) for r in db.execute("SELECT * FROM radar_snapshots WHERE session_date=? ORDER BY timestamp_utc,ticker", (day,))]
        candidates, fallback, policies = {}, {}, set()
        observed_until = None
        for record in db.execute("SELECT timestamp_utc,raw_json FROM radar_batches ORDER BY timestamp_utc"):
            at = instant(record[0])
            if not start <= at <= instant(session.close_utc):
                continue
            raw = json.loads(record[1])
            observed_until = record[0]
            meta = raw.get("metadata", {})
            if meta.get("bar_policy"):
                policies.add(meta["bar_policy"])
            if at < end:
                for item in meta.get("candidates", []):
                    ticker = item["ticker"]
                    c = candidates.setdefault(ticker, dict(ticker=ticker, exchange=item.get("exchange"),
                        candidate_first_detected_at=record[0], candidate_sources=item.get("sources"),
                        source_ranks=item.get("source_ranks"), detection_basis="batch_candidate_universe",
                        symbol_type=item.get("type", "type_unknown"), type_policy=item.get("type_policy"),
                        symbol_type_evidence=item.get("symbol_type_evidence"),
                        excluded_symbol_type=item.get("excluded_symbol_type"), source_history=[]))
                    c["source_history"].append(dict(at=record[0], sources=item.get("sources")))
            # Bar has no open in the current Radar model. Preserve HLCV without guessing O.
            for observation in raw.get("observations", []):
                om = observation.get("metadata", {})
                if om.get("rvol_basis") != "completed_1m" or not om.get("bar_cutoff_utc"):
                    continue
                cutoff = min(instant(om["bar_cutoff_utc"]), at)
                for bar in observation.get("bars", []):
                    bstart, bend = instant(bar["start_utc"]), instant(bar["end_utc"])
                    if start <= bstart < end and bend <= cutoff and (bend-bstart).total_seconds() == 60:
                        fallback[(observation["ticker"], utc(bstart))] = dict(open=None,
                            **{k: bar.get(k) for k in ("high", "low", "close", "volume")},
                            source="radar_batch_completed_1m", source_received_utc=record[0],
                            exchange=om.get("exchange"), bar_start_utc=utc(bstart))
        # Discoveries include unsubscribed candidates outside both TOP10 tables.
        if "radar_discoveries" in names:
            for r in db.execute("SELECT * FROM radar_discoveries WHERE session_date=? ORDER BY first_seen_utc,ticker", (day,)):
                r = dict(r)
                existing = candidates.get(r["ticker"])
                if _inside(r["first_seen_utc"], start, end) and (not existing or instant(r["first_seen_utc"]) < instant(existing["candidate_first_detected_at"])):
                    candidates[r["ticker"]] = dict(ticker=r["ticker"], exchange=None,
                        candidate_first_detected_at=r["first_seen_utc"], candidate_sources=None, source_ranks=None,
                        candidate_sources_session_evidence=unpack(r.get("candidate_sources")),
                        detection_basis="discovery_first_seen", excluded_symbol_type=r.get("excluded_symbol_type"),
                        symbol_type=r.get("symbol_type") or (existing or {}).get("symbol_type", "type_unknown"),
                        type_policy=(existing or {}).get("type_policy"),
                        symbol_type_evidence=unpack(r.get("symbol_type_evidence")) or (existing or {}).get("symbol_type_evidence"),
                        source_history=existing["source_history"] if existing else [])
                if r["ticker"] in candidates:
                    c = candidates[r["ticker"]]
                    c["candidate_tracking_state"] = r.get("candidate_tracking_state")
                    c["discovery_symbol_type_evidence"] = dict(symbol_type=r.get("symbol_type"),
                        excluded_symbol_type=r.get("excluded_symbol_type"),
                        evidence=unpack(r.get("symbol_type_evidence")))
                    if c.get("symbol_type") in {None, "unknown", "type_unknown"} and r.get("symbol_type"):
                        c["symbol_type"] = r["symbol_type"]
                    if r.get("excluded_symbol_type"):
                        c["excluded_symbol_type"] = r["excluded_symbol_type"]
        ranked = [dict(r) for r in db.execute("SELECT * FROM radar_candidates WHERE session_date=? ORDER BY first_detected_utc,ticker", (day,))] if "radar_candidates" in names else []
        for r in ranked:
            if _inside(r["first_detected_utc"], start, end) and r["ticker"] not in candidates:
                candidates[r["ticker"]] = dict(ticker=r["ticker"], exchange=r.get("exchange"),
                    candidate_first_detected_at=r["first_detected_utc"], candidate_sources=None,
                    candidate_sources_session_evidence=unpack(r.get("candidate_sources")),
                    source_ranks=None, detection_basis="radar_candidate", source_history=[],
                    symbol_type=r.get("symbol_type"), symbol_type_evidence=unpack(r.get("symbol_type_evidence")))
        first_batch = db.execute("SELECT MIN(timestamp_utc) FROM radar_batches").fetchone()[0]
        run_id = lifecycle.get("run_id") or digest(dict(identity=identity, first_batch=first_batch))[:32]
        for ticker, c in candidates.items():
            detection = instant(c["candidate_first_detected_at"])
            exact = next((s for s in snapshots if s["ticker"] == ticker and instant(s["timestamp_utc"]) == detection), {})
            rank_record = next((r for r in ranked if r["ticker"] == ticker and instant(r["first_detected_utc"]) == detection), {})
            c["candidate_first_detected_price"] = exact.get("price", rank_record.get("first_price"))
            c["exchange"] = c.get("exchange") or exact.get("exchange") or rank_record.get("exchange")
            for field, target in (("current_leader_rank", "current_rank_at_detection"), ("emerging_rank", "emerging_rank_at_detection"),
                ("day_change_pct", "day_change_at_detection"), ("return_1m", "return_1m_at_detection"),
                ("return_5m", "return_5m_at_detection"), ("rvol", "rvol_at_detection"), ("rvol_status", "rvol_status_at_detection")):
                c[target] = exact.get(field)
            if c.get("candidate_sources") is None:
                c["candidate_sources"] = unpack(exact.get("candidate_sources"))
            if c.get("source_ranks") is None:
                c["source_ranks"] = unpack(exact.get("source_ranks"))
            previous = next((s for s in snapshots if s["ticker"] == ticker and s.get("previous_close") is not None), {})
            c.update(previous_close=previous.get("previous_close"), previous_close_observed_at=previous.get("timestamp_utc"))
        fallback = {k: v for k, v in fallback.items() if k[0] in candidates}
    cache = Path(cache_path or protocol.get("shared_cache_path") or root/"market_radar_cache"/"kiwoom_v2.db").resolve()
    bars = dict(fallback)
    warnings = []
    cache_identity = None
    if cache.exists():
        with copied_database(cache, diagnostics=diagnostics, phase='cache') as db:
            if not {"minute_bars", "cache_metadata"} <= tables(db):
                raise ValueError("Not a minute cache")
            cache_identity = unpack(dict(db.execute("SELECT key,value FROM cache_metadata")).get("identity"), {})
            if cache_identity != dict(provider="kiwoom", mode="real", schema=1, bar_policy=POLICY):
                raise ValueError("Unsupported cache identity/bar policy")
            for ticker, c in candidates.items():
                rows = [dict(r) for r in db.execute("SELECT * FROM minute_bars WHERE ticker=? AND session_date=? ORDER BY received_utc,raw_timestamp", (ticker, day))]
                exchanges = {r["exchange"] for r in rows}
                if not c.get("exchange") and len(exchanges) == 1:
                    c["exchange"] = next(iter(exchanges))
                if not c.get("exchange") and len(exchanges) > 1:
                    warnings.append("ambiguous_cache_exchange:" + ticker)
                    continue
                for r in rows:
                    if r["exchange"] != c.get("exchange"):
                        continue
                    b = _cache_bar(r, start, end)
                    if b:
                        bars[(ticker, b["bar_start_utc"])] = b
    else:
        warnings.append("minute_cache_unavailable")
    if policies and policies != {POLICY}:
        raise ValueError("Unsupported or mixed source bar policies")
    for ticker, c in candidates.items():
        c["bars"] = [v for (t, _), v in sorted(bars.items()) if t == ticker]
        c["paper_v1_trade_ids"], c["rotation_v2_trade_ids"] = [], []
    for path in paper_paths:
        if not Path(path).exists():
            continue
        with copied_database(path, diagnostics=diagnostics, phase='paper_reference') as db:
            if "paper_trades" not in tables(db):
                raise ValueError("Unsupported paper reference database")
            for r in db.execute("SELECT trade_id,ticker,strategy_version FROM paper_trades WHERE session_date_et=? AND radar_run_id=? AND entry_price IS NOT NULL", (day, run_id)):
                if r["ticker"] in candidates:
                    key = {"paper_v1_baseline": "paper_v1_trade_ids", "paper_rotation_v2": "rotation_v2_trade_ids"}.get(r["strategy_version"])
                    if key:
                        candidates[r["ticker"]][key].append(r["trade_id"])
    for c in candidates.values():
        for key in ("paper_v1_trade_ids", "rotation_v2_trade_ids"):
            c[key].sort()
    evidence = dict(session_date_et=day, radar_run_id=run_id, test_version=identity.get("test_version"),
        research_schema_version=metadata.get("schema_version"), source_db_path=str(source), source_cache_path=str(cache),
        source_paper_paths=[str(Path(p).resolve()) for p in paper_paths], cache_identity=cache_identity,
        stock_type_policy=identity.get("stock_type_policy", CONFIG.stock_type_policy),
        stock_type_policy_basis="source_identity" if "stock_type_policy" in identity else "default_config",
        bar_policy=POLICY, calendar_source=calendar.source, regular_open_utc=session.open_utc,
        regular_close_utc=session.close_utc, opening_hour_end_utc=utc(end),
        collection_mode=protocol.get("collection_mode", protocol.get("mode", "unknown")),
        protocol_compliant=protocol.get("protocol_compliant"), source_observed_until=observed_until, warnings=warnings,
        source_lifecycle_stopped_at=lifecycle.get("stopped_at"), candidates=[candidates[t] for t in sorted(candidates)])
    evidence["source_fingerprint"] = digest(logical_evidence(evidence))
    return evidence


def _cache_bar(record, start, end):
    raw = json.loads(record["raw_json"])
    stamp = str(raw.get("cntr_tm", ""))
    if len(stamp) != 14 or not stamp.isdigit() or int(stamp[8:10]) >= 24:
        return None
    try:
        at = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=ET)
        if at.second or not start <= at < end or at+timedelta(minutes=1) > instant(record["received_utc"]):
            return None  # A cached partial minute never becomes completed by waiting.
        prices = dict(zip(("open", "high", "low", "close"),
            (number(raw.get(k), price=True) for k in ("open_pric", "high_pric", "low_pric", "cur_prc"))))
        return dict(prices, volume=number(raw.get("trde_qty")), bar_start_utc=utc(at),
            source="kiwoom_minute_cache", exchange=record["exchange"], source_received_utc=record["received_utc"])
    except (ValueError, TypeError):
        return None
