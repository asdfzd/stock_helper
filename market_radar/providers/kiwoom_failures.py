"""Public error classification; code sets verified against Kiwoom core/errors.py.

Top-level codes (notably 7) do not identify the cause. Inspect the embedded code.
Unknown failures remain unclassified and do not receive immediate retries.
"""
import re

from ..provider import ProviderError, RateLimitError


class StopRequested(ProviderError):
    pass


class RestFailure(ProviderError):
    def __init__(self, details, *, suppressed=False, retry_after=0):
        self.details = details
        self.suppressed = suppressed
        self.retry_after = retry_after
        self.failure_class = details['failure_class']
        super().__init__('Kiwoom REST ' + details['category'])


class RestRateLimitFailure(RestFailure, RateLimitError):
    def __init__(self, details, **kwargs):
        RestFailure.__init__(self, details, **kwargs)
        self.retry_after_seconds = 30


def normalize_code(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def classify(status=None, code=None, message='', category=None):
    match = re.search(r'\[(\d{3,5}):|CODE=(\d{3,5})', message)
    specific = int(match.group(1) or match.group(2)) if match else normalize_code(code)
    if category in {'timeout', 'connection_error'}:
        return 'transient', category, specific
    if status == 429 or specific in {1700, 1701, 1702}:
        return 'transient', 'rate_limited', specific
    if status is not None and 500 <= status <= 599:
        return 'transient', 'server_error', specific
    if status == 401 or specific in {8005, 8031, 8103}:
        return 'transient', 'auth_expired', specific
    if specific in {1901, 1902, 1903}:
        return 'deterministic', 'unsupported', specific
    if specific == 8104:
        return 'deterministic', 'unsupported', specific
    if category == 'no_data':
        return 'deterministic', 'no_data', specific
    if specific in {1501, 1504, 1505, 1511, 1512, 1513, 1514, 1515, 1516, 1517, 1687, 8020}:
        return 'deterministic', 'invalid_request', specific
    return 'unclassified', category or 'api_error', specific
