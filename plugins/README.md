# GB SafeData 플러그인

MCP 클라이언트에 GB SafeData를 연결하는 설정이다. 서버는 stdio로 기동하므로
대부분의 클라이언트가 같은 방식으로 붙는다.

## 사전 준비

```bash
git clone https://github.com/jxkr2026algorix/jxkr2026-gbsafedata
cd jxkr2026-gbsafedata
uv sync --all-packages
cp .env.example .env    # GBSAFE_DATA_GO_KR_SERVICE_KEY 입력
uv run gbsafe doctor    # 연결 상태 확인
```

인증키가 없어도 서버는 기동한다. 카탈로그 검색·검증·인용은 키 없이 동작하고,
실시간 조회는 `not_authorized`로 보고된다.

## Claude Desktop

`claude_desktop_config.json`에 추가한다.

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

[claude-desktop.json](claude-desktop.json)의 내용을 병합하고 `cwd`를 실제 경로로
바꾼다.

## opencode

프로젝트 `opencode.json` 또는 `~/.config/opencode/opencode.json`에
[opencode.json](opencode.json)의 `mcp` 항목을 병합한다.

## Cursor / Windsurf / 기타 MCP 클라이언트

[generic-mcp.json](generic-mcp.json)이 표준 형식이다. 대부분의 클라이언트가
`command` + `args` + `cwd` 조합을 받는다.

## 직접 실행

```bash
uv run gbsafe-mcp        # stdio로 기동
uv run gbsafe mcp        # 동일 (CLI 경유)
```

## Skill 함께 사용하기

`skills/gb-safedata/`를 클라이언트의 스킬 디렉터리에 두면 AI가 데이터를 안전하게
해석하는 규칙까지 함께 적용된다. MCP 서버만 붙이면 도구는 쓸 수 있지만 출처 인용과
안전 경계 규칙이 빠진다.

```bash
# opencode
cp -r skills/gb-safedata ~/.config/opencode/skills/

# Claude Code
cp -r skills/gb-safedata ~/.claude/skills/
```

## 확인

연결 후 다음을 물어보면 동작을 확인할 수 있다.

```
문경시 산사태 위험 상황을 확인해줘
```

기대 결과: 기상특보와 실황 강우가 출처·기준시각과 함께 나오고, 산림청 산사태
API가 심의 대기 중이라는 사실이 함께 보고된다. 후자가 빠지면 Skill이 적용되지
않은 것이다.

## 도구 목록

10개 모두 읽기 전용이다.

| 도구 | 용도 |
| --- | --- |
| `gbsafe_search_datasets` | 데이터셋 검색 (라이선스·심의 상태 포함) |
| `gbsafe_describe_dataset` | 데이터셋 상세 — 취득 방법·결함 |
| `gbsafe_verify_dataset` | 이 용도로 써도 되는지 판정 |
| `gbsafe_resolve_region` | 지역명 → 코드·좌표·기상격자 |
| `gbsafe_hazard_context` | 지역 현재 위험 상황 (다중 원천) |
| `gbsafe_list_sources` | 조회 가능한 원천 목록 |
| `gbsafe_fetch_source` | 원천 직접 조회 |
| `gbsafe_data_health` | 원천 상태 진단 |
| `gbsafe_quality_report` | 확인된 데이터 결함 |
| `gbsafe_population_guidance` | 인구 데이터 사용 가능 여부 |

전화 발신·대피명령·상태변경 도구는 없다. 서버가 기동 시점에 도구 이름을 검사해
그런 도구의 등록을 거부한다.
