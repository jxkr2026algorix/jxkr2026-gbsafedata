#!/usr/bin/env bash
# salgil-aws 자동 재배포. systemd 타이머가 주기적으로 부른다.
#
# main 에 새 커밋이 있을 때만 움직인다. 몇 초마다 도는 것을 감당하려고 평소에는
# ls-remote 로 원격 SHA 한 줄만 물어본다 — 오브젝트 협상이 없어 fetch 보다 훨씬
# 싸다. 실제로 다를 때만 fetch 한다.
#
# 두 저장소 모두 공개라 자격증명이 없다. GitHub 에 배포키를 넣지 않고, 서버에
# 인바운드를 열지도 않는다 — 서버가 밖으로 나가서 가져오기만 한다.
set -euo pipefail

log() { echo "[$(date -Is)] $*"; }

# 서비스 정의: 디렉터리|저장소|배포 명령
SERVICES=(
  "/opt/gbsafedata|https://github.com/jxkr2026algorix/jxkr2026-gbsafedata.git|deploy_gbsafedata"
  "/opt/platform-backend|https://github.com/jxkr2026algorix/jxkr2026-platform-backend.git|deploy_platform"
)

deploy_gbsafedata() {
  docker build -q -t gbsafedata:local . >/dev/null
  docker compose --project-directory . -f deploy/docker-compose.deploy.yml \
    up -d --force-recreate api
}

deploy_platform() {
  docker compose -f docker-compose.yml -f docker-compose.deploy.yml \
    up -d --build --force-recreate api
}

# 지금 돌고 있는 컨테이너 ID. 배포 전후로 비교해 실제로 교체됐는지 본다.
container_id() {
  case "$1" in
    /opt/gbsafedata) docker ps -q --filter name=gbsafedata-api ;;
    /opt/platform-backend) docker ps -q --filter name=jxkr2026-platform-backend-api ;;
  esac
}

# 응답이 온다고 배포가 된 것은 아니다. recreate 가 실패하면 옛 컨테이너가 그대로
# 살아서 헬스체크까지 통과하므로, 응답만 보면 실패가 성공으로 보고된다. 그래서
# 컨테이너가 실제로 바뀌었는지도 함께 본다.
verify() {
  local url="$1" tries=0
  until curl -fsS --max-time 10 "$url" >/dev/null 2>&1; do
    tries=$((tries + 1))
    [ "$tries" -ge 15 ] && return 1
    sleep 2
  done
}

health_url() {
  case "$1" in
    /opt/gbsafedata) echo "http://127.0.0.1:8000/v1/health" ;;
    /opt/platform-backend) echo "http://127.0.0.1:8001/healthz" ;;
  esac
}

for entry in "${SERVICES[@]}"; do
  IFS='|' read -r dir repo deploy_fn <<<"$entry"
  [ -d "$dir/.git" ] || { log "$dir 이 git 저장소가 아닙니다 — 건너뜁니다"; continue; }

  cd "$dir"

  remote_sha=$(git ls-remote --quiet origin refs/heads/main 2>/dev/null | cut -f1)
  [ -n "$remote_sha" ] || { log "$dir: 원격을 읽지 못했습니다 — 건너뜁니다"; continue; }

  local_sha=$(git rev-parse HEAD)
  [ "$local_sha" = "$remote_sha" ] && continue

  git fetch -q origin main || { log "$dir: fetch 실패 — 건너뜁니다"; continue; }

  log "$dir: ${local_sha:0:7} → ${remote_sha:0:7} 배포 시작"
  before_id=$(container_id "$dir")

  # .env 는 서버에만 있다. reset --hard 가 추적 대상만 건드리므로 안전하지만,
  # 실수로 커밋된 적이 있으면 지워질 수 있어 명시적으로 지켜 둔다.
  [ -f .env ] && cp .env /tmp/.env.keep.$$
  git reset -q --hard origin/main
  [ -f /tmp/.env.keep.$$ ] && mv /tmp/.env.keep.$$ .env

  if ! "$deploy_fn" >/dev/null 2>&1; then
    log "$dir: 배포 명령 실패 — 이전 컨테이너를 그대로 둡니다"
    continue
  fi

  if ! verify "$(health_url "$dir")"; then
    log "$dir: 기동했지만 헬스체크가 응답하지 않습니다 — 확인이 필요합니다"
    continue
  fi

  if [ "$(container_id "$dir")" = "$before_id" ]; then
    log "$dir: ${remote_sha:0:7} 반영 (컨테이너 교체 없음 — 이미지에 영향 없는 변경)"
  else
    log "$dir: ${remote_sha:0:7} 배포 완료 (컨테이너 교체됨)"
  fi
done
