from toss_api import TossApiError, get_current_price


def main() -> int:
    try:
        quote = get_current_price("SDOT")
    except TossApiError as exc:
        print(f"error: {exc}")
        if exc.status_code is not None:
            print(f"http_status: {exc.status_code}")
        if exc.retry_after is not None:
            print(f"retry_after: {exc.retry_after}")
        return 1

    print(f"symbol: {quote.symbol}")
    print(f"last_price: {quote.last_price}")
    print(f"timestamp: {quote.timestamp or 'null'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
