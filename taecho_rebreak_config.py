"""병합 태초마을 재돌파 감지와 알림 설정."""

from decimal import Decimal


# 기존 absolute_half -> taecho 병합 기준이다.
TAECHO_MERGE_THRESHOLD = Decimal("0.01")
TAECHO_MERGED_METADATA_KEY = "merged_absolute_half"

# 병합 태초마을 가격에서 이 비율 이상 하락한 뒤 재돌파할 때 알린다.
TAECHO_REBREAK_DROP_THRESHOLD = Decimal("0.10")
TAECHO_REBREAK_APPROACH_THRESHOLD = Decimal("0.05")

# Windows 기본 Beep를 별도 daemon thread에서 순서대로 재생한다.
TAECHO_REBREAK_SOUND_ENABLED = True
TAECHO_REBREAK_SOUND_TONES = ((1000, 250), (1300, 250))
TAECHO_REBREAK_ALERT_DISPLAY_MS = 8000
