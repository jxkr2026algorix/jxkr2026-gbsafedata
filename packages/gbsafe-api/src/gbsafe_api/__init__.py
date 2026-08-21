"""GB SafeData 표준 API — 외부 시스템 연계 표면.

조회 전용이다. 전화 발신·대피명령·상태변경 엔드포인트가 존재하지 않는다.
"""

from __future__ import annotations

from .app import app, create_app
from .envelope import ApiEnvelope, envelope
from .service import HAZARD_PLAYBOOK, SafeDataService, VerificationResult

__all__ = [
    "HAZARD_PLAYBOOK",
    "ApiEnvelope",
    "SafeDataService",
    "VerificationResult",
    "app",
    "create_app",
    "envelope",
]

__version__ = "0.1.0"
