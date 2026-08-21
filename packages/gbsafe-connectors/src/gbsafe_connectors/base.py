"""커넥터 기반 — 원천 호출, 캐시, 스냅샷, 장애 표면화.

이 계층이 해결하는 문제는 "API를 부른다"가 아니라 **부르지 못했을 때 무엇을
돌려줄 것인가**다. 재난 상황에서 조회 실패를 빈 배열로 돌려주면 '위험 없음'으로
읽힌다. 그래서 모든 결과가 `FetchOutcome`이고, 실패는 `Degradation`으로 남는다.

기관별 함정을 여기서 흡수한다(../jxkr2026-datasets/docs/api-operations.md):

- 서비스키 파라미터 이름이 기관마다 `ServiceKey`/`serviceKey`로 다르다
- 응답 포맷 파라미터가 `dataType`/`returnType`/`_type`으로 갈린다
- 국립중앙의료원·산불발생통계는 XML만 반환한다
- AirKorea는 간헐적으로 504를 반환하며 개발계정 한도가 일 500건이다
- 게이트웨이 오류는 HTTP 200 본문에 `<returnReasonCode>`로 온다

호출 한도를 지키기 위해 캐시를 먼저 보고, 실패 시 마지막 정상 스냅샷으로
폴백하되 그 사실을 `UpstreamStatus.CACHED`/`DEGRADED`로 알린다.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, ClassVar
from xml.etree import ElementTree

import httpx
from gbsafe_core.catalog import Catalog, DatasetEntry, get_catalog
from gbsafe_core.config import CREDENTIAL_SOURCES, CredentialName, Settings, get_settings
from gbsafe_core.freshness import evaluate as evaluate_freshness
from gbsafe_core.freshness import unknown as unknown_freshness
from gbsafe_core.models import (
    DataMode,
    Degradation,
    LicenseCode,
    Provenance,
    QualityFlag,
    Record,
    SourceOutcome,
    SourceReceipt,
    UpstreamStatus,
)
from gbsafe_core.snapshot import SnapshotStore

KST = timezone(timedelta(hours=9))

#: 포털 게이트웨이가 HTTP 200 본문에 실어 보내는 오류 코드.
_GATEWAY_CODE = re.compile(r"<returnReasonCode>(\d+)</returnReasonCode>")
_GATEWAY_MSG = re.compile(r"<returnAuthMsg>([^<]+)</returnAuthMsg>")
_RESULT_CODE = re.compile(r'"?resultCode"?\s*[:>]\s*"?(\d+)"?')
_RESULT_MSG = re.compile(r'"?resultMsg"?\s*[:>]\s*"?([^",<]+)')

#: 인증키 미등록. 신청 직후 활성화 대기이거나 심의 대기 상태다.
_NOT_REGISTERED = ("SERVICE_KEY_IS_NOT_REGISTERED", "등록되지 않은")
_QUOTA_EXCEEDED = ("LIMITED_NUMBER", "허용량을 초과")


class ConnectorError(RuntimeError):
    """커넥터가 결과를 만들 수 없는 상태. 호출자에게 이유를 전달한다."""


@dataclass(frozen=True, slots=True)
class RawResponse:
    """원천의 응답 본문과 그것을 어떻게 얻었는지."""

    body: bytes
    content_type: str
    endpoint: str
    status: UpstreamStatus
    retrieved_at: datetime
    snapshot_id: str | None = None
    detail: str = ""

    def text(self) -> str:
        """본문을 문자열로. CP949 파일데이터도 다룬다."""
        for encoding in ("utf-8", "cp949", "euc-kr"):
            try:
                return self.body.decode(encoding)
            except UnicodeDecodeError:
                continue
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.text())

    def xml(self) -> ElementTree.Element:
        return ElementTree.fromstring(self.text())


@dataclass(slots=True)
class FetchOutcome[PayloadT]:
    """조회 결과.

    `outcome`이 빈 결과의 의미를 명시한다. 파서는 원천이 정상 응답으로
    "해당 없음"을 밝힌 경우에만 `confirmed_empty()`를 반환해야 하고, 응답 구조를
    알아보지 못한 경우는 실패다. 이 구별이 없으면 파싱 실패가 '위험 없음'이 된다.
    """

    records: tuple[Record[PayloadT], ...] = ()
    degradations: tuple[Degradation, ...] = ()
    caveats: tuple[str, ...] = ()
    confirmed_absence: bool = False

    @property
    def ok(self) -> bool:
        return not any(item.blocks_interpretation for item in self.degradations)

    @property
    def outcome(self) -> SourceOutcome:
        if self.records:
            return SourceOutcome.RECORDS
        if self.ok and self.confirmed_absence:
            return SourceOutcome.CONFIRMED_EMPTY
        return SourceOutcome.FAILED

    @property
    def is_empty_but_healthy(self) -> bool:
        """원천이 '해당 없음'을 명시했는지. 파싱 실패는 여기 해당하지 않는다."""
        return self.outcome is SourceOutcome.CONFIRMED_EMPTY

    def merge(self, other: FetchOutcome[PayloadT]) -> FetchOutcome[PayloadT]:
        return FetchOutcome(
            records=self.records + other.records,
            degradations=self.degradations + other.degradations,
            caveats=tuple(dict.fromkeys(self.caveats + other.caveats)),
            confirmed_absence=self.confirmed_absence and other.confirmed_absence,
        )

    def receipt(self, *, connector: str, dataset_id: str) -> SourceReceipt:
        """이 조회의 영수증을 만든다."""
        status = (
            self.degradations[0].status
            if self.degradations
            else UpstreamStatus.OK
        )
        detail = self.degradations[0].detail if self.degradations else ""
        if not self.records and not self.confirmed_absence and not self.degradations:
            detail = "원천이 '해당 없음'을 명시하지 않았습니다 — 응답을 해석하지 못했을 수 있습니다"
        return SourceReceipt(
            connector=connector,
            dataset_id=dataset_id,
            outcome=self.outcome,
            record_count=len(self.records),
            checked_at=datetime.now(UTC),
            upstream_status=status,
            detail=detail,
        )


def confirmed_empty(*caveats: str) -> FetchOutcome[Any]:
    """원천이 정상 응답으로 '해당 없음'을 밝힌 경우.

    응답 구조를 알아보지 못한 경우에는 쓰지 않는다.
    """
    return FetchOutcome(caveats=caveats, confirmed_absence=True)


@dataclass(slots=True)
class _CacheEntry:
    response: RawResponse
    expires_at: float


class _MemoryCache:
    """프로세스 내 TTL 캐시 + 동시 요청 병합.

    캐시만으로는 부족하다. 캐시가 비어 있는 동안 동시에 들어온 요청 N개가
    각각 원천을 호출하면 한도가 N배로 소진된다(cache stampede). AirKorea는
    개발계정 한도가 일 500건이므로 동시 요청 20개면 4%가 한 번에 사라진다.

    그래서 같은 키에 대한 첫 호출만 실제로 나가고, 나머지는 그 결과를
    기다린다(single-flight).
    """

    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}
        self._inflight: dict[str, asyncio.Future[RawResponse]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> RawResponse | None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._entries[key]
                return None
            return entry.response

    async def put(self, key: str, response: RawResponse, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        async with self._lock:
            self._entries[key] = _CacheEntry(response, time.monotonic() + ttl_seconds)

    async def claim(self, key: str) -> tuple[asyncio.Future[RawResponse], bool]:
        """이 키의 호출 권한을 요청한다.

        반환값의 두 번째 요소가 True면 호출자가 실제로 원천을 호출하고
        `settle()`로 결과를 알려야 한다. False면 반환된 future를 기다린다.
        """
        async with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                return existing, False
            future: asyncio.Future[RawResponse] = asyncio.get_running_loop().create_future()
            self._inflight[key] = future
            return future, True

    async def settle(
        self, key: str, response: RawResponse | None, error: BaseException | None
    ) -> None:
        """진행 중인 호출을 완료 처리하고 대기자를 깨운다."""
        async with self._lock:
            future = self._inflight.pop(key, None)
        if future is None or future.done():
            return
        if error is not None:
            future.set_exception(error)
        elif response is not None:
            future.set_result(response)

    async def clear(self) -> None:
        async with self._lock:
            self._entries.clear()
            self._inflight.clear()


_CACHE = _MemoryCache()

#: 캐시 TTL 하한/상한. 갱신주기가 없거나 극단적인 데이터셋을 보정한다.
MIN_TTL_SECONDS = 60.0
MAX_TTL_SECONDS = 3600.0

#: 인증키 파라미터에 쓰이는 이름들. 스냅샷·로그에서 제거 대상이다.
SECRET_PARAM_NAMES = frozenset({"serviceKey", "ServiceKey", "authKey", "apiKey", "key"})


class Connector[PayloadT](ABC):
    """하나의 데이터셋을 조회해 정규화된 레코드로 돌려주는 단위."""

    dataset_id: ClassVar[str]
    credential: ClassVar[CredentialName | None] = CredentialName.DATA_GO_KR
    service_key_param: ClassVar[str] = "serviceKey"

    #: 지역을 지정할 때 이 커넥터가 받는 인자 이름.
    #:
    #: 기관마다 다르다 — 기상청은 격자를 계산할 `location`, 응급의료는 `sigungu`,
    #: 도로변 산사태는 주소 문자열 `address`, 파일데이터는 `region`이다. 호출부가
    #: 하나로 통일해 넘기면 이름이 맞지 않는 커넥터에서 **필터가 조용히 무시되어**
    #: 시군 단위 질의에 도 전체 결과가 돌아온다. None이면 지역 지정을 받지 않는다.
    region_param: ClassVar[str | None] = None

    #: 이 원천의 실제 갱신 간격(초). 포털 메타데이터에 갱신주기가 비어 있는
    #: 경우가 많아, 커넥터가 아는 실제 주기를 신선도 판정에 쓴다. None이면
    #: 카탈로그 값을 따르고 그것도 없으면 절대 임계값으로 판정한다.
    update_cycle_seconds: ClassVar[int | None] = None

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        catalog: Catalog | None = None,
        store: SnapshotStore | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._catalog = catalog or get_catalog()
        self._store = store or SnapshotStore.from_settings(self._settings)
        self._client = client

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def entry(self) -> DatasetEntry | None:
        return self._catalog.get(self.dataset_id)

    @property
    def dataset_name(self) -> str:
        entry = self.entry
        return entry.name if entry else self.dataset_id

    @property
    def provider(self) -> str:
        entry = self.entry
        return entry.provider if entry else "미확인"

    @property
    def available(self) -> bool:
        """지금 호출할 수 있는지. 키가 없으면 False."""
        if self.credential is None:
            return True
        return self._settings.has(self.credential)

    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        assert self.credential is not None
        return (
            f"{self.credential.value} 인증키가 없습니다. "
            f"발급: {CREDENTIAL_SOURCES[self.credential]}"
        )

    @abstractmethod
    def base_url(self) -> str:
        """호출할 엔드포인트."""

    @abstractmethod
    def build_params(self, **kwargs: Any) -> dict[str, str]:
        """인증키를 제외한 요청 파라미터."""

    @abstractmethod
    def parse(self, response: RawResponse, **kwargs: Any) -> FetchOutcome[PayloadT]:
        """원천 응답을 정규화된 레코드로 변환."""

    @classmethod
    def region_kwargs(cls, region: str | None) -> dict[str, Any]:
        """지역 값을 이 커넥터가 이해하는 인자로 변환한다.

        지역 지정을 받지 않는 커넥터에는 빈 dict를 준다. 무시될 인자를 넘기는
        것보다 넘기지 않는 편이 낫다 — 호출자가 필터가 적용됐다고 오해하지 않는다.
        """
        if region is None or cls.region_param is None:
            return {}
        return {cls.region_param: region}

    async def fetch(self, **kwargs: Any) -> FetchOutcome[PayloadT]:
        """조회한다. 예외를 밖으로 던지지 않고 degradation으로 변환한다."""
        reason = self.unavailable_reason()
        if reason is not None:
            return FetchOutcome(
                degradations=(
                    Degradation(
                        dataset_id=self.dataset_id,
                        status=UpstreamStatus.NOT_AUTHORIZED,
                        detail=reason,
                        occurred_at=datetime.now(UTC),
                        last_known_good_at=self._last_snapshot_time(),
                    ),
                )
            )

        try:
            response = await self._request(**kwargs)
        except ConnectorError as error:
            return self._degrade(UpstreamStatus.UNAVAILABLE, str(error))
        except KeyError as error:
            # build_params가 요구하는 인자가 없는 경우. 호출자 실수이므로
            # 스택 트레이스가 아니라 무엇이 필요한지 알려준다.
            return self._degrade(
                UpstreamStatus.UNAVAILABLE,
                f"필수 인자가 없습니다: {error}. 이 원천은 해당 인자 없이 조회할 수 없습니다.",
            )
        except ValueError as error:
            return self._degrade(UpstreamStatus.UNAVAILABLE, f"인자가 올바르지 않습니다: {error}")

        if response.status is UpstreamStatus.NOT_AUTHORIZED:
            return self._degrade(
                UpstreamStatus.NOT_AUTHORIZED, self._explain_denial(response.detail)
            )
        if response.status is UpstreamStatus.UNAVAILABLE:
            return self._degrade(UpstreamStatus.UNAVAILABLE, response.detail)
        if response.status is UpstreamStatus.DEGRADED:
            # 한도 초과·resultCode 오류 응답을 파싱하면 대개 빈 목록이 나오고,
            # 그것이 '자료 없음'으로 보고된다. 파싱 전에 실패로 확정한다.
            return self._degrade(
                UpstreamStatus.DEGRADED,
                response.detail or "원천이 오류 응답을 반환했습니다",
            )

        try:
            outcome = self.parse(response, **kwargs)
        except (
            json.JSONDecodeError,
            ElementTree.ParseError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            return self._degrade(
                UpstreamStatus.DEGRADED,
                f"응답을 해석할 수 없습니다 ({type(error).__name__}: {error}). "
                "원천 응답 형식이 변경되었을 수 있습니다.",
            )

        if response.status is UpstreamStatus.CACHED:
            outcome = FetchOutcome(
                records=outcome.records,
                degradations=outcome.degradations,
                caveats=(*outcome.caveats, "캐시된 응답입니다 (호출 한도 보호)"),
            )
        return outcome

    async def _request(self, **kwargs: Any) -> RawResponse:
        params = self.build_params(**kwargs)
        url = self.base_url()
        cache_key = f"{url}?{sorted(params.items())}"

        cached = await _CACHE.get(cache_key)
        if cached is not None:
            return _as_cached(cached)

        # 같은 조회가 동시에 들어오면 첫 요청만 원천을 호출한다.
        waiter, is_leader = await _CACHE.claim(cache_key)
        if not is_leader:
            return _as_cached(await waiter)
        try:
            response = await self._request_uncached(cache_key, params, url)
        except BaseException as error:
            await _CACHE.settle(cache_key, None, error)
            raise
        await _CACHE.settle(cache_key, response, None)
        return response

    async def _request_uncached(
        self, cache_key: str, params: dict[str, str], url: str
    ) -> RawResponse:
        """실제 호출. 동시 요청 병합은 `_request`가 담당한다."""
        if self._settings.offline:
            latest = self._store.latest(self.dataset_id)
            if latest is None:
                raise ConnectorError(
                    "오프라인 모드이며 저장된 스냅샷이 없습니다. "
                    "GBSAFE_OFFLINE=false로 두고 한 번 수집하세요."
                )
            body = self._store.get(self.dataset_id, latest.snapshot_id)
            if not body:
                raise ConnectorError(
                    f"저장된 스냅샷({latest.snapshot_id[:12]})을 읽을 수 없습니다. "
                    "파일이 손상되었거나 삭제되었습니다."
                )
            return RawResponse(
                body=body,
                content_type=latest.content_type,
                endpoint=latest.endpoint or url,
                status=UpstreamStatus.CACHED,
                retrieved_at=latest.stored_at,
                snapshot_id=latest.snapshot_id,
                detail="오프라인 모드 — 저장된 스냅샷",
            )

        request_params = dict(params)
        if self.credential is not None:
            key = self._settings.credential(self.credential)
            if key:
                request_params[self.service_key_param] = key

        response = await self._send(url, request_params)
        # 정상 응답만 보존한다. 오류 본문을 스냅샷에 남기면 나중에 그것이
        # '마지막 정상자료'로 제시된다.
        if response.status is UpstreamStatus.OK:
            ref = self._store.put(
                dataset_id=self.dataset_id,
                body=response.body,
                content_type=response.content_type,
                endpoint=url,
                request_params=params,
            )
            response = RawResponse(
                body=response.body,
                content_type=response.content_type,
                endpoint=response.endpoint,
                status=response.status,
                retrieved_at=response.retrieved_at,
                snapshot_id=ref.snapshot_id,
                detail=response.detail,
            )
            await _CACHE.put(cache_key, response, self._ttl_seconds())
        return response

    async def _send(self, url: str, params: dict[str, str]) -> RawResponse:
        attempts = self._settings.http_max_retries + 1
        last_detail = ""
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                timeout=self._settings.http_timeout_seconds,
                headers={"User-Agent": "gbsafedata/0.1 (+https://github.com/jxkr2026algorix)"},
                follow_redirects=True,
            )
        try:
            for attempt in range(attempts):
                try:
                    raw = await client.get(url, params=params)
                except (httpx.TimeoutException, httpx.TransportError) as error:
                    last_detail = f"{type(error).__name__}: {error}"
                    if attempt + 1 < attempts:
                        await asyncio.sleep(min(2.0**attempt, 4.0))
                        continue
                    return RawResponse(
                        body=b"",
                        content_type="",
                        endpoint=url,
                        status=UpstreamStatus.UNAVAILABLE,
                        retrieved_at=datetime.now(UTC),
                        detail=f"원천에 연결할 수 없습니다 — {last_detail}",
                    )

                body = raw.content
                content_type = raw.headers.get("content-type", "application/octet-stream")
                status, detail = _classify(body, raw.status_code)

                # AirKorea가 간헐적으로 반환하는 504는 재시도하면 응답한다
                if status is UpstreamStatus.UNAVAILABLE and attempt + 1 < attempts:
                    last_detail = detail
                    await asyncio.sleep(min(2.0**attempt, 4.0))
                    continue

                return RawResponse(
                    body=body,
                    content_type=content_type,
                    endpoint=url,
                    status=status,
                    retrieved_at=datetime.now(UTC),
                    detail=detail,
                )
        finally:
            if owns_client:
                await client.aclose()

        return RawResponse(
            body=b"",
            content_type="",
            endpoint=url,
            status=UpstreamStatus.UNAVAILABLE,
            retrieved_at=datetime.now(UTC),
            detail=last_detail or "알 수 없는 오류",
        )

    def _explain_denial(self, detail: str) -> str:
        """권한 거부의 실제 원인을 설명한다.

        같은 403이 '키 없음'과 '개발단계 심의 대기'를 모두 의미할 수 있고,
        후자는 신청해도 승인 전까지 계속 막힌다. 구별해 주지 않으면
        사용자가 키를 다시 발급받는 헛수고를 한다.
        """
        entry = self.entry
        if entry is not None and not entry.dev_ready:
            return (
                f"{detail} — 「{entry.name}」은 개발단계가 심의승인 대상입니다. "
                "활용신청이 승인되기 전까지 호출이 거부됩니다 "
                f"(신청: {entry.url or 'data.go.kr'}). "
                "승인 소요기간은 공개되지 않아 미리 신청해 두어야 합니다."
            )
        if entry is not None and entry.access_route.value == "external_portal":
            portal = entry.external_portal or "원천기관 포털"
            return (
                f"{detail} — 「{entry.name}」은 data.go.kr이 카탈로그 역할만 하고 "
                f"실제 인증키는 {portal}에서 별도로 발급받아야 합니다."
            )
        return (
            f"{detail} — 인증키가 이 데이터셋에 대해 활성화되지 않았습니다. "
            "활용신청 여부와 활성화 대기(최대 1시간)를 확인하세요."
        )

    def _expected_cycle_seconds(self) -> int | None:
        """신선도 판정에 쓸 갱신주기. 커넥터가 아는 값이 카탈로그보다 정확하다."""
        if self.update_cycle_seconds is not None:
            return self.update_cycle_seconds
        entry = self.entry
        return entry.update_cycle_seconds if entry else None

    def _ttl_seconds(self) -> float:
        cycle = self._expected_cycle_seconds()
        base = float(cycle) if cycle else MIN_TTL_SECONDS * 5
        scaled = base * self._settings.cache_ttl_factor
        return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, scaled))

    def _last_snapshot_time(self) -> datetime | None:
        latest = self._store.latest(self.dataset_id)
        return latest.stored_at if latest else None

    def _degrade(self, status: UpstreamStatus, detail: str) -> FetchOutcome[PayloadT]:
        return FetchOutcome(
            degradations=(
                Degradation(
                    dataset_id=self.dataset_id,
                    status=status,
                    detail=detail,
                    occurred_at=datetime.now(UTC),
                    last_known_good_at=self._last_snapshot_time(),
                ),
            )
        )

    def provenance(
        self,
        response: RawResponse,
        *,
        observed_at: datetime | None = None,
        published_at: datetime | None = None,
        mode: DataMode = DataMode.REAL,
    ) -> Provenance:
        """이 커넥터의 출처 정보를 만든다."""
        entry = self.entry
        return Provenance(
            dataset_id=self.dataset_id,
            dataset_name=self.dataset_name,
            provider=self.provider,
            source_url=entry.url if entry else None,
            endpoint=response.endpoint,
            license=entry.license if entry else LicenseCode.UNKNOWN,
            mode=DataMode.SNAPSHOT if response.status is UpstreamStatus.CACHED else mode,
            upstream_status=response.status,
            retrieved_at=response.retrieved_at,
            observed_at=observed_at,
            published_at=published_at,
            expected_cycle_seconds=self._expected_cycle_seconds(),
            snapshot_id=response.snapshot_id,
        )

    def record(
        self,
        payload: PayloadT,
        response: RawResponse,
        *,
        observed_at: datetime | None = None,
        published_at: datetime | None = None,
        quality_flags: tuple[QualityFlag, ...] = (),
        notes: tuple[str, ...] = (),
        mode: DataMode = DataMode.REAL,
    ) -> Record[PayloadT]:
        """정규화된 값을 출처·신선도와 함께 감싼다."""
        provenance = self.provenance(
            response, observed_at=observed_at, published_at=published_at, mode=mode
        )
        entry = self.entry
        freshness = (
            evaluate_freshness(
                as_of=provenance.effective_time,
                expected_cycle_seconds=provenance.expected_cycle_seconds,
            )
            if observed_at or published_at
            else unknown_freshness()
        )
        flags = tuple(dict.fromkeys(quality_flags + (entry.quality_flags if entry else ())))
        return Record(
            payload=payload,
            provenance=provenance,
            freshness=freshness,
            quality_flags=flags,
            notes=notes,
        )


def _classify(body: bytes, status_code: int) -> tuple[UpstreamStatus, str]:
    """응답 본문에서 실제 성공 여부를 판정한다.

    포털은 오류를 HTTP 200 본문에 담아 보내므로 상태코드만으로는 알 수 없다.
    """
    if status_code in (401, 403):
        return UpstreamStatus.NOT_AUTHORIZED, f"HTTP {status_code} — 인증 실패"
    if status_code == 429:
        return UpstreamStatus.DEGRADED, "HTTP 429 — 호출 한도 초과"
    if status_code >= 500:
        return UpstreamStatus.UNAVAILABLE, f"HTTP {status_code} — 원천 서버 오류"
    if status_code >= 400:
        return UpstreamStatus.UNAVAILABLE, f"HTTP {status_code}"

    text = body.decode("utf-8", "ignore")

    gateway = _GATEWAY_CODE.search(text)
    if gateway and gateway.group(1) != "00":
        message = _GATEWAY_MSG.search(text)
        detail = f"게이트웨이 오류 {gateway.group(1)}"
        if message:
            detail = f"{detail} — {message.group(1)}"
        if any(needle in text for needle in _NOT_REGISTERED):
            return UpstreamStatus.NOT_AUTHORIZED, f"{detail} (인증키 미등록·승인 대기)"
        return UpstreamStatus.UNAVAILABLE, detail

    if any(needle in text for needle in _NOT_REGISTERED):
        return (
            UpstreamStatus.NOT_AUTHORIZED,
            "인증키가 등록되지 않았습니다 — 활성화 대기 또는 심의 대기 상태입니다",
        )
    if any(needle in text for needle in _QUOTA_EXCEEDED):
        return UpstreamStatus.DEGRADED, "일일 호출 허용량을 초과했습니다"

    result = _RESULT_CODE.search(text)
    if result and result.group(1) not in ("00", "0"):
        message = _RESULT_MSG.search(text)
        detail = f"resultCode {result.group(1)}"
        if message:
            detail = f"{detail} — {message.group(1).strip()}"
        return UpstreamStatus.DEGRADED, detail

    return UpstreamStatus.OK, ""


#: 캐시 표시로 덮어써서는 안 되는 상태. 이 값을 CACHED로 바꾸면
#: 권한 거부·장애가 '정상 조회, 결과 없음'으로 읽힌다.
_PRESERVED_STATUSES = frozenset(
    {
        UpstreamStatus.NOT_AUTHORIZED,
        UpstreamStatus.UNAVAILABLE,
        UpstreamStatus.DEGRADED,
    }
)


def _as_cached(response: RawResponse) -> RawResponse:
    """캐시·병합으로 재사용된 응답임을 표시한다.

    실패 상태는 보존한다. 403을 CACHED로 바꾸면 호출자가 '조회 성공, 자료 없음'
    으로 해석해 권한 거부가 '위험 없음'이 된다.
    """
    if response.status is UpstreamStatus.CACHED or response.status in _PRESERVED_STATUSES:
        return response
    return RawResponse(
        body=response.body,
        content_type=response.content_type,
        endpoint=response.endpoint,
        status=UpstreamStatus.CACHED,
        retrieved_at=response.retrieved_at,
        snapshot_id=response.snapshot_id,
        detail=response.detail,
    )


async def clear_cache() -> None:
    await _CACHE.clear()
