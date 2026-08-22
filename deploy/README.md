# 배포

서버: `ubuntu@salgil-aws` (tailnet). 공개 주소: `https://datainfra.salgil.gyeongbuk.kr`

## 갱신 절차

```bash
# 1) 코드 동기화 — .env는 서버에만 있으므로 반드시 제외한다
rsync -az --delete \
  --exclude='.git' --exclude='.venv' --exclude='.env' \
  --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.ruff_cache' \
  ./ ubuntu@salgil-aws:/opt/gbsafedata/

# 2) 재빌드 후 교체
ssh ubuntu@salgil-aws
cd /opt/gbsafedata
docker build -t gbsafedata:local .
docker compose -f deploy/docker-compose.deploy.yml up -d --force-recreate api
```

**compose 파일은 `deploy/` 경로로 부른다.** 예전에는 서버 루트(`/opt/gbsafedata/docker-compose.deploy.yml`)에 복사본을 두고 그걸 썼는데, 그 파일은 레포에 그 위치로 존재하지 않는다. 그래서 `--delete`가 붙은 rsync가 "레포에 없는 파일"로 판단해 지워버리고, 다음 `docker compose` 호출이 통째로 실패한다. 레포 안의 경로를 그대로 쓰면 rsync가 알아서 최신으로 유지하므로 이 문제가 생기지 않는다.

## 반영 확인

```bash
curl -s https://datainfra.salgil.gyeongbuk.kr/v1/health | jq .summary
```

`available` 수치가 바뀐 코드와 맞는지 본다. 컨테이너가 healthy여도 **이전 이미지로 떠 있을 수 있다** — 빌드가 성공했는데 recreate가 실패하면 옛 컨테이너가 그대로 살아 있고, 헬스체크는 통과한다. 배포 여부는 컨테이너 상태가 아니라 응답 내용으로 판단한다.

## Caddy

리버스 프록시는 이 compose에 없다. 서버의 여러 서비스가 공유하므로 시스템 서비스로 따로 돈다.

```bash
journalctl -u caddy -f
sudo systemctl reload caddy
```

설정은 [`deploy/caddy/`](caddy)에 있고 서버의 `/etc/caddy/`에 대응한다. 서비스를 추가할 때는 `/etc/caddy/sites/<이름>.caddy` 파일만 떨어뜨리면 되며, 이 서비스를 건드리거나 재시작할 필요가 없다.

**Cloudflare SSL/TLS 모드는 `Full`이어야 한다.** `Full (strict)`는 실패한다 — 오리진 인증서가 Caddy 내부 CA 자체서명이라 Cloudflare가 검증하지 못한다.
