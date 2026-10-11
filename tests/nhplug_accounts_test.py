"""Synthetic account inquiry fixtures only; no user .env, network or evidence DB."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.nhplug_client_test import Backend
from market_radar.execution.nhplug_accounts import NhplugAccountProbe, _balance_page, _quantity_result
from market_radar.execution.nhplug_client import (
    _OfficialSdk, NhplugClientError, ACCOUNTS_ENDPOINT, QUOTE_ENDPOINT,
    BALANCE_ENDPOINT, BUYABLE_ENDPOINT, guard_endpoint,
)
from tools.nhplug_probe.accounts import main

A, B, C = "11111111234", "22222221234", "33333331234"  # Same masked suffix on purpose.


def balance(rows=()):
    return {"rsp_msg": "정상처리되었습니다", "Output_0": {"fc_eal_amt": 0}, "Output_1": list(rows)}


def quantity(n=0):
    return {"rsp_msg": "정상처리되었습니다", "Output_0": {"byn_pbl_qty": n, "orr_pbl_qty": n,
                                                              "max_pbl_qty": n + 1 if isinstance(n, (int, float)) else n}}


class AccountBackend(Backend):
    def __init__(self):
        super().__init__()
        self.pages = {A: balance(), B: {"rsp_msg": "해외주식 조회 대상 계좌가 아닙니다"}, C: balance()}
        self.quantity_response = quantity()

    def call(self, path, body):
        self.calls.append((path, dict(body)))
        if path == ACCOUNTS_ENDPOINT:
            return {"Output_0": [{"acct_no": number, "acct_type": code}
                                 for number, code in [(A, '01'), (B, '02'), (C, '01'), (A, '01'), (B, '03')]]}
        if path == QUOTE_ENDPOINT:
            return {"Output_0": {"iem_cd": "AAPL", "trdprc": 123.45}}
        if path == BUYABLE_ENDPOINT:
            return self.quantity_response
        raise AssertionError("Unexpected endpoint")

    def inquiry_page(self, path, body, **kwargs):
        self.calls.append((path, dict(body)))
        value = self.pages[body['act_no']]
        if isinstance(value, Exception):
            raise value
        return value, SimpleNamespace(has_next=False)


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.backend = AccountBackend()
        self.client = NhplugAccountProbe(backend=self.backend)

    def test_zero_balance_is_usable_not_failure(self):
        self.assertEqual(_balance_page(balance())['status'], 'PASS')
        self.assertEqual(_balance_page(balance())['positions'], 0)

    def test_explicit_empty_without_output_block(self):
        result = _balance_page({'rsp_msg': '조회된 잔고가 없습니다'})
        self.assertEqual((result['status'], result['positions']), ('PASS', 0))

    def test_absent_output_never_assumed_zero(self):
        for data in ({}, {'rsp_msg': '정상처리되었습니다'}, {'Output_1': []}):
            self.assertEqual(_balance_page(data)['status'], 'FAIL')
            self.assertIsNone(_balance_page(data)['positions'])

    def test_account_rejection_is_not_usable(self):
        result = _balance_page({'rsp_msg': '해외주식 조회 대상 계좌가 아닙니다 ' + A})
        self.assertEqual(result['status'], 'NOT_USABLE')
        self.assertNotIn(A, repr(result))

    def test_unknown_business_error_is_fail(self):
        self.assertEqual(_balance_page({'rsp_msg': '시스템 오류'})['status'], 'FAIL')

    def test_positions_require_valid_quantity(self):
        self.assertEqual(_balance_page(balance([{'iem_cd': 'TEST', 'cns_bse_bnc_qty': 3}]))['positions'], 1)
        self.assertEqual(_balance_page(balance([{'iem_cd': 'TEST', 'cns_bse_bnc_qty': 0}]))['positions'], 0)
        self.assertEqual(_balance_page(balance([{'iem_cd': 'TEST'}]))['status'], 'FAIL')

    def test_quantity_fields_are_distinct_and_zero_is_valid(self):
        result = _quantity_result(quantity(0))
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['buyable_quantity'], 0)
        self.assertEqual(result['max_buyable_quantity'], 1)

    def test_missing_invalid_or_failed_quantity_not_zero(self):
        for data in ({'rsp_msg': '정상처리되었습니다', 'Output_0': {}}, quantity('NaN'),
                     quantity(-1), quantity(True), quantity('1.5'), {'rsp_msg': '조회 실패'}):
            self.assertEqual(_quantity_result(data)['status'], 'FAIL')

    def test_full_run_checks_all_live_accounts_and_no_persistent_selection(self):
        result = self.client.inspect_overseas_accounts()
        self.assertEqual(result['live_accounts'], 3)
        self.assertEqual(result['usable_overseas_accounts'], 2)
        self.assertIsNone(result['selected_account'])
        self.assertEqual(result['selection'], 'user_choice_required')
        self.assertEqual(len({a['account_ref'] for a in result['accounts']}), 3)
        for number in (A, B, C):
            self.assertNotIn(number, json.dumps(result))
        balances = [body for path, body in self.backend.calls if path == BALANCE_ENDPOINT]
        self.assertEqual([b['act_no'] for b in balances], [A, B, C])
        self.assertTrue(all(b['fc_sec_trd_nat_cd'] == '200' and b['cur_cd'] == 'USD' for b in balances))
        quantities = [body for path, body in self.backend.calls if path == BUYABLE_ENDPOINT]
        self.assertEqual([b['act_no'] for b in quantities], [A, C])
        self.assertTrue(all(b['pcs_dit'] == '2' and b['fc_orr_uit_pr'] == 123.45 and
                            b['wtm_cur_knd_cd'] == '2' for b in quantities))
        self.assertEqual(result['order_calls'], 0)
        self.assertEqual(self.backend.socket.sent, [])

    def test_transport_error_not_classified_account_rejection(self):
        self.backend.pages[A] = RuntimeError('token=fake-token account=' + A)
        result = self.client.inspect_overseas_accounts()
        self.assertEqual(result['accounts'][0]['balance']['status'], 'FAIL')
        self.assertNotIn('fake-token', repr(result))
        self.assertNotIn(A, repr(result))
        self.assertEqual(result['selected_account'], 'live-3')

    def test_pagination_collects_all_positions(self):
        meta1 = SimpleNamespace(has_next=True, cts='synthetic-next', cts_flag='Y')
        meta2 = SimpleNamespace(has_next=False)
        page = balance([{'iem_cd': 'TEST', 'cns_bse_bnc_qty': 1}])
        with patch.object(self.backend, 'inquiry_page', side_effect=[(page, meta1), (page, meta2)]) as call:
            self.assertEqual(self.client._balance(A)['positions'], 2)
            self.assertEqual(call.call_args.kwargs['cts'], 'synthetic-next')

    def test_repeating_pagination_fails_instead_of_partial_success(self):
        with patch.object(self.backend, 'inquiry_page', return_value=(balance(),
                SimpleNamespace(has_next=True, cts='same', cts_flag='Y'))):
            self.assertEqual(self.client._balance(A)['status'], 'FAIL')

    def test_buyable_pcs_guard_and_no_order_endpoints(self):
        backend = _OfficialSdk.__new__(_OfficialSdk)
        backend.sdk = SimpleNamespace(call=lambda *a, **k: self.fail('unexpected call'))
        for pcs in ('1', '3', '4', '5', None):
            with self.assertRaisesRegex(NhplugClientError, 'buyable_quantity_inquiry_only'):
                backend.call(BUYABLE_ENDPOINT, {'pcs_dit': pcs})
        for path in ('/gbstock/order/v1/buy', '/gbstock/order/v1/sell', '/modify', '/cancel', '/reservation'):
            with self.assertRaises(NhplugClientError):
                guard_endpoint(path)

    def test_cli_masks_accounts_and_never_calls_ws(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(client_factory=lambda: self.client), 0)
        for number in (A, B, C):
            self.assertNotIn(number, output.getvalue())
        self.assertIn('********1234', output.getvalue())
        self.assertEqual(self.backend.socket.sent, [])


if __name__ == '__main__':
    unittest.main()
