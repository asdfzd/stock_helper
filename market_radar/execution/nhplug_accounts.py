"""Explicit read-only US account inspection; never selects a persistent default.

Contract: https://www.nhplug.com/openapi-docs/gbstock/openapi.json
Balance: Output_0 summary, Output_1 positions; buyableAmount pcs_dit=2.
Full account numbers live only in local variables used for the authenticated call.
"""
from decimal import Decimal, InvalidOperation
import re

from .nhplug_client import (
    NhplugClient, NhplugClientError, ACCOUNTS_ENDPOINT, BALANCE_ENDPOINT,
    BUYABLE_ENDPOINT, _price, _ticker,
)


def _integer(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        if number.is_finite() and number >= 0 and number == number.to_integral_value():
            return int(number)
    except (InvalidOperation, ValueError):
        pass
    return None


def _message_state(data):
    """Read business text, return only fixed local labels and a bounded code.

    Official SDK deliberately does not define universal REST success codes.
    A transport/auth error never proves an account is unusable.
    """
    envelope = data.get("message")
    envelope = envelope if isinstance(envelope, dict) else {}
    messages = [data.get("rsp_msg"), envelope.get("usr_msg")]
    text = " ".join(m for m in messages if isinstance(m, str))
    code = data.get("rsp_cd") or envelope.get("msg_code")
    code = code if isinstance(code, str) and re.fullmatch(r"(?:[0-9]{5}|IGW[0-9]{5})", code) else None
    # Require explicit account-level rejection; do not infer it from zero money,
    # an unknown business code, missing output, timeout, or an HTTP status.
    unavailable = re.search(
        r"계좌.*(?:사용.*불가|이용.*불가|조회.*불가|거래.*불가|처리.*불가|대상.*아[닌니]|존재하지|없는 계좌)|"
        r"(?:해외.*(?:미등록|미약정|신청.*않|등록.*않)|잘못된 계좌|대상 계좌가 아[닌니닙])", text)
    if unavailable:
        return "NOT_USABLE", "account_rejected", code
    if any(data.get(k) for k in ("error", "error_code", "error_description")) or re.search(
            r"오류|실패|불가|유효하지|권한.*없|미등록|denied|invalid|error|failed", text, re.I):
        return "FAIL", "api_business_error", code
    if re.search(r"(?:잔고|보유종목|조회.*내역|조회.*자료|조회.*데이터).*(?:없|존재하지)", text):
        return "EMPTY", "explicit_no_positions", code
    if re.search(r"정상|조회.*완료|success", text, re.I):
        return "OK", None, code
    return "UNKNOWN", "business_status_unconfirmed", code


def _balance_page(data):
    if not isinstance(data, dict):
        return dict(status="FAIL", positions=None, reason="malformed_response", response_code=None)
    state, reason, code = _message_state(data)
    result = dict(status="FAIL", positions=None, reason=reason, response_code=code)
    if state in {"FAIL", "NOT_USABLE"}:
        result["status"] = state
        return result
    rows = data.get("Output_1")
    if rows is None and state == "EMPTY":
        result.update(status="PASS", positions=0)
        return result
    if not isinstance(rows, list):
        result["reason"] = "position_block_missing_or_malformed"
        return result
    if state not in {"OK", "EMPTY"}:
        return result  # Never interpret an unknown business result as empty balance.
    counts = []
    for row in rows:
        quantity = _integer(row.get("cns_bse_bnc_qty")) if isinstance(row, dict) else None
        if quantity is None or not isinstance(row.get("iem_cd"), str) or not row["iem_cd"].strip():
            result["reason"] = "position_row_malformed"
            return result
        counts.append(quantity > 0)
    result.update(status="PASS", positions=sum(counts), reason=None)
    return result


def _quantity_result(data):
    result = dict(status="FAIL", buyable_quantity=None, orderable_quantity=None,
                  max_buyable_quantity=None, reason="malformed_response", response_code=None)
    if not isinstance(data, dict):
        return result
    state, reason, code = _message_state(data)
    result.update(reason=reason, response_code=code)
    if state not in {"OK", "EMPTY"}:
        return result
    output = data.get("Output_0")
    if not isinstance(output, dict):
        result["reason"] = "quantity_block_missing"
        return result
    fields = {"byn_pbl_qty": "buyable_quantity", "orr_pbl_qty": "orderable_quantity",
              "max_pbl_qty": "max_buyable_quantity"}
    for source, target in fields.items():
        if source in output:
            number = _integer(output[source])
            if number is None:
                result["reason"] = "quantity_invalid"
                return result
            result[target] = number
    if all(result[target] is None for target in fields.values()):
        result["reason"] = "quantity_missing"
    else:
        result.update(status="PASS", reason=None)
    return result


class NhplugAccountProbe(NhplugClient):
    """One-shot account validation; same read-only transport, no WS methods called."""

    def _balance(self, account):
        body = dict(act_no=account, qut_iqr_dit_cd="9", fc_sec_trd_nat_cd="200",
                    cur_cd="USD", xns_dit_cd="1")
        cts = cts_flag = None
        seen = set()
        total = 0
        for _ in range(20):
            data, meta = self._invoke(lambda: self._backend.inquiry_page(
                BALANCE_ENDPOINT, body, cts=cts, cts_flag=cts_flag))
            result = _balance_page(data)
            if result["status"] != "PASS":
                return result
            total += result["positions"]
            if not meta.has_next:
                result["positions"] = total
                return result
            cts, cts_flag = meta.cts, meta.cts_flag
            if not cts or cts in seen:
                break
            seen.add(cts)
        return dict(status="FAIL", positions=None, reason="incomplete_pagination", response_code=None)

    def _buyable_quantity(self, account, ticker, price):
        # pcs_dit=2 is inquiry only. The quote price parameter does NOT submit an
        # order; wtm_cur_knd_cd=2 follows the official SDK's KRW-margin example.
        if _price(price) is None:
            raise NhplugClientError("invalid_inquiry_price")
        body = dict(act_no=account, pcs_dit="2", fc_sec_trd_nat_cd="200", iem_cd=ticker,
                    wtm_cur_knd_cd="2", oss_orr_knd_cd="1", ahi_nmn_pr_tp_cd="00",
                    fc_orr_uit_pr=price)
        return _quantity_result(self._invoke(lambda: self._backend.call(BUYABLE_ENDPOINT, body)))

    def inspect_overseas_accounts(self, ticker="AAPL"):
        ticker = _ticker(ticker)
        data = self._request(ACCOUNTS_ENDPOINT, {})
        rows = data.get("Output_0")
        if not isinstance(rows, list):
            raise NhplugClientError("accounts_missing_or_malformed")
        live = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise NhplugClientError("account_row_malformed")
            if row.get("acct_type") not in {"01", "02"}:
                continue
            number = row.get("acct_no")
            if not isinstance(number, str) or not re.fullmatch(r"[0-9]{11}", number):
                raise NhplugClientError("account_number_malformed")
            if number not in seen:
                live.append(number)
                seen.add(number)
        output = []
        eligible = []
        for index, number in enumerate(live, 1):
            try:
                balance = self._balance(number)
            except NhplugClientError as exc:
                balance = dict(status="FAIL", positions=None, reason=str(exc), response_code=None)
            item = dict(account_ref=f"live-{index}", account=self.mask_account(number), balance=balance,
                        buyable=dict(status="NOT_RUN", reason="balance_not_verified"))
            output.append(item)
            if balance["status"] == "PASS":
                eligible.append((number, item))
        try:
            quote = self.overseas_current_price(ticker)
        except NhplugClientError as exc:
            quote = dict(provider="nhplug", ticker=ticker, price=None, reason=str(exc))
        for number, item in eligible:
            if quote["price"] is None:
                item["buyable"] = dict(status="FAIL", reason="quote_unavailable")
                continue
            try:
                item["buyable"] = self._buyable_quantity(number, ticker, quote["price"])
            except NhplugClientError as exc:
                item["buyable"] = dict(status="FAIL", reason=str(exc))
        return dict(live_accounts=len(live), accounts=output, usable_overseas_accounts=len(eligible),
                    quote=quote, selected_account=eligible[0][1]["account_ref"] if len(eligible) == 1 else None,
                    selection="user_choice_required" if len(eligible) > 1 else
                              ("unique_usable_account" if eligible else "no_verified_account"),
                    order_calls=0)
