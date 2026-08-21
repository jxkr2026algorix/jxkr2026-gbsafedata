"""GB SafeData 표준 API — 외부 시스템 연계 표면.

조회 전용이다. 전화 발신·대피명령·상태변경 엔드포인트가 존재하지 않는다.
"""

from __future__ import annotations

from .app import create_app
from .envelope import ApiEnvelope, envelope
from .service import HAZARD_PLAYBOOK, SafeDataService, VerificationResult


def __getattr__(name: str) -> object:
    """`app`을 접근 시점에 만든다. 설정 오류가 import를 깨뜨리지 않게 한다."""
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [  # `app`은 __getattr__로 지연 생성된다
    "HAZARD_PLAYBOOK",
    "ApiEnvelope",
    "SafeDataService",
    "VerificationResult",
    "create_app",
    "envelope",
]

__version__ = "0.1.0"
