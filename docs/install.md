# 설치 가이드

AI 하네스에 GB SafeData를 붙이는 방법. 처음이라면 [한 줄 설치](#한-줄-설치)만 보면 된다.

**MCP 서버와 Skill을 함께 설치해야 한다.** MCP만 붙이면 도구는 동작하지만, AI가 조회 실패를 '위험 없음'으로 답할 수 있다. Skill이 그것을 막는 규칙을 준다.

---

## 사전 준비

| 필요한 것 | 확인 | 없을 때 |
| --- | --- | --- |
| `uv` | `uv --version` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Python 3.12+ | `python3 --version` | uv가 자동으로 받는다 |
| `git` | `git --version` | macOS `xcode-select --install` |

인증키는 없어도 설치된다. 카탈로그 검색·라이선스 검증·인용은 키 없이 동작하고, 실시간 조회만 키가 필요하다.

## 한 줄 설치

```bash
curl -fsSL https://raw.githubusercontent.com/jxkr2026algorix/jxkr2026-gbsafedata/main/install.sh | bash
```

하네스를 감지해 현재 프로젝트에 MCP 서버와 Skill을 붙인다. 저장소는 `~/.gbsafedata`에 받는다.

```bash
# 사용자 전역에 설치 (모든 프로젝트에서 사용)
curl -fsSL .../install.sh | bash -s -- --global

# 하네스를 직접 지정
curl -fsSL .../install.sh | bash -s -- --harness claude-code

# 제거
curl -fsSL .../install.sh | bash -s -- --uninstall
```

설치 후 이렇게 물어본다.

```
문경시 산사태 위험 상황을 확인해줘
```

**제대로 붙었다면 조회하지 못한 원천을 밝히고, 확인하지 않은 위험을 '없음'으로 답하지 않는다.** 그 문장이 없으면 Skill이 로드되지 않은 것이다.

## 하네스별 수동 설치

스크립트를 쓰지 않거나 설정을 직접 관리하려면 아래를 따른다. `PATH`는 저장소를 받은 실제 경로로 바꾼다.

```bash
git clone https://github.com/jxkr2026algorix/jxkr2026-gbsafedata ~/.gbsafedata
cd ~/.gbsafedata && uv sync --all-packages
```

### opencode

프로젝트에 적용하려면 `opencode.json`, 전역이면 `~/.config/opencode/opencode.json`에 넣는다.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "gbsafedata": {
      "type": "local",
      "command": ["uv", "run", "gbsafe-mcp"],
      "cwd": "/absolute/path/to/.gbsafedata",
      "enabled": true
    }
  }
}
```

Skill은 디렉터리를 복사한다.

```bash
mkdir -p .opencode/skills                                    # 전역이면 ~/.config/opencode/skills
cp -r ~/.gbsafedata/skills/gb-safedata .opencode/skills/
```

저장소 안에서 작업한다면 `cwd`를 생략할 수 있다.

### Claude Code

```bash
claude mcp add gbsafedata --scope project -- uv run --directory ~/.gbsafedata gbsafe-mcp
mkdir -p .claude/skills && cp -r ~/.gbsafedata/skills/gb-safedata .claude/skills/
```

`--scope user`로 하면 전역이다. `.mcp.json`을 직접 쓰려면:

```json
{
  "mcpServers": {
    "gbsafedata": {
      "command": "uv",
      "args": ["run", "gbsafe-mcp"],
      "cwd": "/absolute/path/to/.gbsafedata"
    }
  }
}
```

### Claude Desktop

`claude_desktop_config.json`을 편집한 뒤 앱을 완전히 종료하고 다시 켠다.

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gbsafedata": {
      "command": "uv",
      "args": ["run", "gbsafe-mcp"],
      "cwd": "C:\\path\\to\\.gbsafedata"
    }
  }
}
```

Claude Desktop은 Skill 디렉터리를 지원하지 않는다. [`skills/gb-safedata/SKILL.md`](../skills/gb-safedata/SKILL.md) 내용을 프로젝트 지침에 붙여 넣거나, Skill이 필요하면 Claude Code를 쓴다.

### Cursor / Windsurf

`.cursor/mcp.json` (전역은 `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "gbsafedata": {
      "command": "uv",
      "args": ["run", "gbsafe-mcp"],
      "cwd": "/absolute/path/to/.gbsafedata"
    }
  }
}
```

### 그 외 MCP 클라이언트

stdio로 붙는 클라이언트는 [`plugins/generic-mcp.json`](../plugins/generic-mcp.json)을 참고한다. 핵심은 `uv run gbsafe-mcp`를 저장소 디렉터리에서 실행하는 것뿐이다.

## 인증키

키가 없으면 실시간 조회가 `not_authorized`로 보고된다 — 크래시가 아니라 정상 동작이다.

```bash
cd ~/.gbsafedata
cp .env.example .env
# GBSAFE_DATA_GO_KR_SERVICE_KEY=... 입력
uv run gbsafe doctor
```

발급: [data.go.kr](https://www.data.go.kr) 가입 → 원하는 데이터셋에서 **활용신청** → 마이페이지 > 인증키 발급현황. 자동승인이면 최대 1시간 뒤 활성화된다.

**이 키 하나로 6종 원천이 동작한다.** 나머지 키는 선택이며 [docs/data-sources.md](data-sources.md)에 발급 경로가 있다.

`doctor`가 원천별로 왜 못 쓰는지 구별해 알려준다.

| 표시 | 뜻 | 할 일 |
| --- | --- | --- |
| OK | 동작 | — |
| 인증키 필요 | 키가 없음 | 위 절차대로 발급 |
| 개발단계 심의 대기 | 신청했으나 미승인 | 기다린다. 키를 다시 받아도 해결되지 않는다 |
| CSV 수동 취득 | 포털에서 직접 내려받아야 함 | `gbsafe normalize-csv` 로 정규화 |

## 설치 확인

```bash
cd ~/.gbsafedata
uv run gbsafe doctor          # 원천 상태
uv run gbsafe region 문경시    # 좌표·격자 변환
uv run gbsafe hazard 문경시 --type landslide
```

MCP 연결을 직접 확인하려면:

```bash
uv run python - <<'PY'
import asyncio, json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command="uv", args=["run", "gbsafe-mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            tools = await session.list_tools()
            print(f"{info.server_info.name}: {len(tools.tools)} tools")
            result = await session.call_tool("gbsafe_resolve_region", {"region": "문경시"})
            print(json.loads(result.content[0].text)["kma_grid"])

asyncio.run(main())
PY
```

`{'nx': 81, 'ny': 106}`이 나오면 정상이다.

## 문제 해결

**도구가 보이지 않는다.** 하네스를 재시작한다. Claude Desktop은 완전 종료가 필요하다. 설정 파일이 유효한 JSON인지 확인한다 — `python3 -m json.tool < opencode.json`.

**`uv: command not found`.** 하네스가 `uv`를 PATH에서 못 찾는 경우다. GUI 앱(Claude Desktop)은 셸 PATH를 물려받지 않으므로 절대 경로를 쓴다 — `command`를 `/Users/you/.local/bin/uv`처럼 적는다. `which uv`로 확인한다.

**전부 `not_authorized`.** 키가 없거나 활성화 전이다. `uv run gbsafe keys`로 무엇이 없는지 본다.

**산사태만 실패한다.** 정상이다. 산림청 산사태 3종은 개발단계가 심의승인이라 승인 전까지 막힌다. 다른 API와 방향이 반대이며 키를 다시 받아도 해결되지 않는다.

**AI가 "산사태 위험 없음"이라고 답한다.** Skill이 로드되지 않았다. `.opencode/skills/gb-safedata/SKILL.md`가 있는지 확인한다. MCP만 붙으면 이 답이 나올 수 있는데, 조회 실패를 안전으로 바꿔치기한 것이므로 반드시 고쳐야 한다.

**응답이 비어 있다.** `absence_confirmed`를 본다. `false`면 원천이 '해당 없음'을 확인해 준 것이 아니라 조회에 실패한 것이다. `receipts`에서 어느 원천이 `failed`인지 확인한다.

**대기질만 실패한다.** AirKorea 서버가 불안정해 간헐적으로 504와 `resultCode 04`를 반환한다. 재시도해도 3회 중 1회는 실패하므로 대피 판단의 필수 의존으로 두지 않는다.

**전부 타임아웃된다.** 네트워크나 리전 문제일 수 있다. GitHub 호스티드 러너(미국)에서는 대체로 응답하지만 간헐적으로 전체 타임아웃이 관측됐다. `uv run python scripts/smoke_live_apis.py`가 원천 전체 도달 불가와 개별 결함을 구별해 알려준다.

## 실사용 예시 (프로젝트 스코프)

작업 디렉터리에 저장소를 두고 쓰는 구성이다.

```bash
cd ~/your-workspace
git clone https://github.com/jxkr2026algorix/jxkr2026-gbsafedata gbsafedata
cd gbsafedata
uv sync --all-packages
cp .env.example .env    # 키 입력

# 프로젝트 스코프 설정 — 저장소 안이므로 cwd 생략 가능
cat > opencode.json <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "gbsafedata": {
      "type": "local",
      "command": ["uv", "run", "gbsafe-mcp"],
      "enabled": true
    }
  }
}
JSON

mkdir -p .opencode/skills
cp -r skills/gb-safedata .opencode/skills/

opencode
```

이제 세션에서 "문경시 산사태 위험 확인해줘"를 물어보면 MCP와 Skill이 함께 적용된다.
