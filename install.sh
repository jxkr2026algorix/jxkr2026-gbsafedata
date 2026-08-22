#!/usr/bin/env bash
# GB SafeData를 AI 하네스에 설치한다.
#
# 하네스를 감지해 MCP 서버와 Skill을 함께 붙인다. 둘 중 하나만 붙이면
# 도구는 동작하지만 AI가 빈 결과를 '위험 없음'으로 답할 수 있다.
#
#   curl -fsSL https://raw.githubusercontent.com/jxkr2026algorix/jxkr2026-gbsafedata/main/install.sh | bash
#
# 또는 저장소를 받은 뒤:
#   ./install.sh                 # 현재 프로젝트에 설치 (권장)
#   ./install.sh --global        # 사용자 전역에 설치
#   ./install.sh --harness opencode
#   ./install.sh --uninstall

set -euo pipefail

REPO_URL="https://github.com/jxkr2026algorix/jxkr2026-gbsafedata"
SERVER_NAME="gbsafedata"

SCOPE="project"
HARNESS=""
UNINSTALL=0

info()  { printf '\033[0;36m→\033[0m %s\n' "$*"; }
ok()    { printf '\033[0;32m✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[0;33m!\033[0m %s\n' "$*"; }
fail()  { printf '\033[0;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --global)    SCOPE="global" ;;
    --project)   SCOPE="project" ;;
    --harness)   HARNESS="${2:-}"; shift ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help)   usage ;;
    *)           fail "알 수 없는 옵션: $1 (--help 참고)" ;;
  esac
  shift
done

# ── 저장소 위치 ──────────────────────────────────────────────────
# 스크립트가 저장소 안에 있으면 그것을 쓰고, 아니면 받아온다.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if [ -f "$SCRIPT_DIR/packages/gbsafe-mcp/pyproject.toml" ]; then
  REPO_DIR="$SCRIPT_DIR"
else
  REPO_DIR="${GBSAFE_HOME:-$HOME/.gbsafedata}"
  if [ -d "$REPO_DIR/.git" ]; then
    info "기존 설치를 갱신합니다: $REPO_DIR"
    git -C "$REPO_DIR" pull --quiet --ff-only || warn "갱신 실패 — 기존 버전을 씁니다"
  else
    command -v git >/dev/null 2>&1 || fail "git이 필요합니다"
    info "저장소를 받습니다: $REPO_DIR"
    git clone --quiet --depth 1 "$REPO_URL" "$REPO_DIR"
  fi
fi

SKILL_SRC="$REPO_DIR/skills/gb-safedata"
[ -d "$SKILL_SRC" ] || fail "Skill을 찾을 수 없습니다: $SKILL_SRC"

# ── 하네스 감지 ──────────────────────────────────────────────────
detect_harness() {
  [ -n "$HARNESS" ] && { echo "$HARNESS"; return; }
  if [ -f "opencode.json" ] || [ -f "opencode.jsonc" ] || [ -d ".opencode" ]; then
    echo "opencode"; return
  fi
  if [ -d ".claude" ] || [ -f "CLAUDE.md" ]; then echo "claude-code"; return; fi
  if command -v opencode >/dev/null 2>&1; then echo "opencode"; return; fi
  if command -v claude >/dev/null 2>&1; then echo "claude-code"; return; fi
  echo "unknown"
}

HARNESS="$(detect_harness)"

# ── 경로 결정 ────────────────────────────────────────────────────
case "$HARNESS" in
  opencode)
    if [ "$SCOPE" = "global" ]; then
      CONFIG_FILE="$HOME/.config/opencode/opencode.json"
      SKILL_DIR="$HOME/.config/opencode/skills"
    else
      CONFIG_FILE="opencode.json"
      SKILL_DIR=".opencode/skills"
    fi
    ;;
  claude-code)
    if [ "$SCOPE" = "global" ]; then
      CONFIG_FILE="$HOME/.claude.json"
      SKILL_DIR="$HOME/.claude/skills"
    else
      CONFIG_FILE=".mcp.json"
      SKILL_DIR=".claude/skills"
    fi
    ;;
  claude-desktop)
    case "$(uname -s)" in
      Darwin) CONFIG_FILE="$HOME/Library/Application Support/Claude/claude_desktop_config.json" ;;
      *)      CONFIG_FILE="$HOME/.config/Claude/claude_desktop_config.json" ;;
    esac
    SKILL_DIR=""
    ;;
  cursor)
    if [ "$SCOPE" = "global" ]; then CONFIG_FILE="$HOME/.cursor/mcp.json"; else CONFIG_FILE=".cursor/mcp.json"; fi
    SKILL_DIR=""
    ;;
  *)
    fail "하네스를 감지할 수 없습니다. --harness 로 지정하세요 (opencode | claude-code | claude-desktop | cursor)"
    ;;
esac

# ── 제거 ─────────────────────────────────────────────────────────
if [ "$UNINSTALL" = "1" ]; then
  if [ -f "$CONFIG_FILE" ]; then
    python3 - "$CONFIG_FILE" "$SERVER_NAME" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
except (OSError, json.JSONDecodeError):
    sys.exit(0)
for key in ("mcp", "mcpServers"):
    if isinstance(config.get(key), dict):
        config[key].pop(name, None)
with open(path, "w", encoding="utf-8") as handle:
    json.dump(config, handle, indent=2, ensure_ascii=False)
    handle.write("\n")
PY
    ok "MCP 설정에서 제거: $CONFIG_FILE"
  fi
  if [ -n "$SKILL_DIR" ] && [ -d "$SKILL_DIR/gb-safedata" ]; then
    rm -rf "$SKILL_DIR/gb-safedata"
    ok "Skill 제거: $SKILL_DIR/gb-safedata"
  fi
  exit 0
fi

# ── 사전 조건 ────────────────────────────────────────────────────
command -v uv >/dev/null 2>&1 || fail "uv가 필요합니다 — https://docs.astral.sh/uv/getting-started/installation/"
command -v python3 >/dev/null 2>&1 || fail "python3가 필요합니다"

info "의존성을 설치합니다"
(cd "$REPO_DIR" && uv sync --all-packages --quiet) || fail "uv sync 실패"

# ── MCP 등록 ─────────────────────────────────────────────────────
mkdir -p "$(dirname "$CONFIG_FILE")"
python3 - "$CONFIG_FILE" "$HARNESS" "$REPO_DIR" "$SERVER_NAME" <<'PY'
import json
import sys
from pathlib import Path

path, harness, repo, name = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]

config: dict[str, object] = {}
if path.is_file():
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = path.with_suffix(path.suffix + ".gbsafe-backup")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  기존 설정을 읽을 수 없어 백업했습니다: {backup}")

if harness == "opencode":
    # opencode 는 McpLocalConfig 스키마를 쓴다.
    entry = {"type": "local", "command": ["uv", "run", "gbsafe-mcp"], "cwd": repo, "enabled": True}
    config.setdefault("$schema", "https://opencode.ai/config.json")
    servers = config.setdefault("mcp", {})
else:
    entry = {"command": "uv", "args": ["run", "gbsafe-mcp"], "cwd": repo}
    servers = config.setdefault("mcpServers", {})

if not isinstance(servers, dict):
    raise SystemExit("설정의 MCP 항목이 객체가 아닙니다 — 수동으로 확인하세요")

servers[name] = entry
path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
ok "MCP 서버 등록: $CONFIG_FILE"

# ── Skill 설치 ───────────────────────────────────────────────────
if [ -n "$SKILL_DIR" ]; then
  mkdir -p "$SKILL_DIR"
  rm -rf "$SKILL_DIR/gb-safedata"
  cp -R "$SKILL_SRC" "$SKILL_DIR/gb-safedata"
  ok "Skill 설치: $SKILL_DIR/gb-safedata"
else
  warn "$HARNESS 는 Skill을 지원하지 않습니다 — MCP 도구는 동작하지만"
  warn "AI가 빈 결과를 '위험 없음'으로 답할 수 있습니다"
fi

# ── 인증키 ───────────────────────────────────────────────────────
if [ ! -f "$REPO_DIR/.env" ]; then
  cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
  warn "인증키가 없습니다. 실시간 조회를 쓰려면 입력하세요:"
  printf '    %s\n' "$REPO_DIR/.env"
  printf '    GBSAFE_DATA_GO_KR_SERVICE_KEY=...\n'
  printf '    발급: https://www.data.go.kr 마이페이지 > 인증키 발급현황\n'
else
  ok "인증키 파일 확인: $REPO_DIR/.env"
fi

# ── 확인 ─────────────────────────────────────────────────────────
info "설치를 검증합니다"
if (cd "$REPO_DIR" && uv run gbsafe doctor >/dev/null 2>&1); then
  ok "CLI 동작 확인"
else
  warn "gbsafe doctor 실패 — 'cd $REPO_DIR && uv run gbsafe doctor' 로 확인하세요"
fi

echo
ok "설치 완료 ($HARNESS, $SCOPE scope)"
echo
echo "다음을 물어보세요:"
echo '    문경시 산사태 위험 상황을 확인해줘'
echo
echo "제대로 붙었다면 조회하지 못한 원천을 밝히고, 확인하지 않은 위험을"
echo "'없음'으로 답하지 않습니다. 그 문장이 없으면 Skill이 로드되지 않은 것입니다."
