"""Live UI background worker 설정."""

# Toss MARKET_DATA는 공식적으로 더 높은 TPS를 지원하지만, UI 표시 목적의
# batch polling은 보수적으로 2초 간격을 사용한다. 1.0으로 쉽게 조정 가능하다.
PRICE_REFRESH_INTERVAL_SECONDS = 2.0

# 등록된 종목의 현재가를 반복 조회하고 UI의 실시간 감지 상태를 갱신한다.
# False로 바꾸면 캡처 시 단발 조회만 유지되고 주기적 polling은 중단된다.
ENABLE_REALTIME_PRICE_POLLING = True

# UpCuit 전체 거래정지 목록을 한 번 받아 등록 종목과 대조한다.
# 5초 간격이면 분당 12회로 공개 API의 권장 한도(약 60회/분)보다 낮다.
HALT_REFRESH_INTERVAL_SECONDS = 5.0
