# syntax=docker/dockerfile:1
#
# GB SafeData API 컨테이너.
#
# 빌드 단계와 실행 단계를 나눈다. uv와 빌드 캐시가 최종 이미지에 남으면 크기가
# 커지고, 그 안에 형제 저장소 경로 같은 빌드 시점 정보가 함께 남는다.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 의존성을 먼저 설치해 소스만 바뀌었을 때 이 레이어를 재사용한다.
COPY pyproject.toml uv.lock ./
COPY packages/gbsafe-core/pyproject.toml packages/gbsafe-core/
COPY packages/gbsafe-connectors/pyproject.toml packages/gbsafe-connectors/
COPY packages/gbsafe-api/pyproject.toml packages/gbsafe-api/
COPY packages/gbsafe-mcp/pyproject.toml packages/gbsafe-mcp/
COPY packages/gbsafe-cli/pyproject.toml packages/gbsafe-cli/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages --no-install-workspace

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages


FROM python:3.12-slim-bookworm AS runtime

# 루트로 돌리지 않는다. 이 프로세스는 인터넷에 노출되고 외부 API를 호출한다.
RUN useradd --create-home --uid 10001 gbsafe

WORKDIR /app
COPY --from=build --chown=gbsafe:gbsafe /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GBSAFE_STORE_DIR=/var/lib/gbsafe

RUN mkdir -p /var/lib/gbsafe && chown gbsafe:gbsafe /var/lib/gbsafe
VOLUME ["/var/lib/gbsafe"]

USER gbsafe
EXPOSE 8000

# /v1/health는 인증을 켜도 열려 있다. 막으면 로드밸런서가 정상 인스턴스를 죽인다.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=8).status == 200 else 1)"

CMD ["uvicorn", "gbsafe_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
