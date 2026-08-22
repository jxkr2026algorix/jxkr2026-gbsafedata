"""환경 설정. 키가 없는 상태를 오류가 아니라 '기능 축소'로 다룬다.

키 부재는 정상적인 운영 상태다(심의 대기·신청 전). 따라서 설정 로딩이
실패하지 않고, 대신 어떤 커넥터가 왜 쓸 수 없는지 조회할 수 있어야 한다.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CredentialName(StrEnum):
    """커넥터가 요구할 수 있는 인증 정보의 이름."""

    DATA_GO_KR = "data_go_kr_service_key"
    KMA_APIHUB = "kma_apihub_auth_key"
    HRFCO = "hrfco_service_key"
    SAFETYDATA = "safetydata_service_key"
    SAFEMAP = "safemap_api_key"
    ITS = "its_api_key"
    VWORLD = "vworld_api_key"
    SGIS_KEY = "sgis_consumer_key"
    SGIS_SECRET = "sgis_consumer_secret"


#: 각 인증 정보를 어디서 발급받는지. doctor 명령과 오류 메시지에 그대로 쓴다.
CREDENTIAL_SOURCES: dict[CredentialName, str] = {
    CredentialName.DATA_GO_KR: (
        "https://www.data.go.kr — 마이페이지 > 인증키 발급현황 (활성화 최대 1시간)"
    ),
    CredentialName.KMA_APIHUB: "https://apihub.kma.go.kr/join.do — 가입 즉시 발급 (휴대전화 인증)",
    CredentialName.HRFCO: (
        "https://www.hrfco.go.kr/web/openapiPage/certifyKey.do — 이메일 승인 클릭 필요"
    ),
    CredentialName.SAFETYDATA: "https://www.safetydata.go.kr — 이용신청 후 승인",
    CredentialName.SAFEMAP: (
        "https://www.safemap.go.kr/opna/crtfc/keyAgreeRenew.do — 도메인 고정 발급"
    ),
    CredentialName.ITS: "https://www.its.go.kr — 오픈API 신청",
    CredentialName.VWORLD: "https://www.vworld.kr — 인증키 발급",
    CredentialName.SGIS_KEY: "https://sgis.kostat.go.kr/developer — ONE-ID 로그인 후 발급",
    CredentialName.SGIS_SECRET: "https://sgis.kostat.go.kr/developer — ONE-ID 로그인 후 발급",
}


class Settings(BaseSettings):
    """`GBSAFE_` 접두사 환경변수 또는 `.env`에서 읽는다."""

    model_config = SettingsConfigDict(
        env_prefix="GBSAFE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    data_go_kr_service_key: SecretStr | None = None
    data_go_kr_service_key_encoded: SecretStr | None = None
    kma_apihub_auth_key: SecretStr | None = None
    hrfco_service_key: SecretStr | None = None
    safetydata_service_key: SecretStr | None = None
    safemap_api_key: SecretStr | None = None
    its_api_key: SecretStr | None = None
    vworld_api_key: SecretStr | None = None
    sgis_consumer_key: SecretStr | None = None
    sgis_consumer_secret: SecretStr | None = None

    store_dir: Path = Field(default=Path("var/gbsafe"))
    cache_ttl_factor: float = Field(default=1.0, gt=0)
    offline: bool = False
    http_timeout_seconds: float = Field(default=20.0, gt=0)
    http_max_retries: int = Field(default=3, ge=0)

    #: 브라우저에서 직접 부를 수 있는 출처. 쉼표로 구분한다.
    #:
    #: 기본값은 빈 목록이라 CORS 헤더가 나가지 않는다. `*`를 기본으로 두면
    #: 배포하는 순간 아무 사이트나 이 API를 통해 우리 인증키로 원천을 호출할 수
    #: 있게 된다. 대시보드 도메인을 명시적으로 적어야 열린다.
    cors_allow_origins: str = ""

    #: API 키. 쉼표로 구분하며, 비어 있으면 인증을 걸지 않는다.
    #:
    #: 로컬 개발과 CI는 키 없이 돌아야 하므로 기본은 무인증이다. 다만 인터넷에
    #: 노출하는 순간 이 값을 반드시 채워야 한다 — 이 API는 우리 정부 인증키로
    #: 원천을 부르므로, 열어두면 우리 호출 한도를 남이 소진한다.
    api_keys: str = ""

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return tuple(
            item.strip() for item in self.cors_allow_origins.split(",") if item.strip()
        )

    @property
    def accepted_api_keys(self) -> frozenset[str]:
        return frozenset(
            item.strip() for item in self.api_keys.split(",") if item.strip()
        )

    def credential(self, name: CredentialName) -> str | None:
        """평문 값을 반환한다. 없으면 None.

        로깅·직렬화 경로에서는 절대 호출하지 않는다.
        """
        secret: SecretStr | None = getattr(self, name.value, None)
        if secret is None:
            return None
        value = secret.get_secret_value().strip()
        return value or None

    def has(self, name: CredentialName) -> bool:
        return self.credential(name) is not None

    def missing(self) -> tuple[CredentialName, ...]:
        return tuple(name for name in CredentialName if not self.has(name))

    def available(self) -> tuple[CredentialName, ...]:
        return tuple(name for name in CredentialName if self.has(name))

    @property
    def snapshot_dir(self) -> Path:
        return self.store_dir / "snapshots"

    @property
    def cache_dir(self) -> Path:
        return self.store_dir / "cache"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """테스트에서 환경변수를 바꾼 뒤 재로딩하기 위한 훅."""
    get_settings.cache_clear()
