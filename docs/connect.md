# 내 AI에 연결하기

경북 재난 공공데이터를 AI에게 붙이는 방법. 설치할 것도, 가입할 것도, 인증키도 없습니다.

**주소 하나만 있으면 됩니다.**

```
https://datainfra.salgil.gyeongbuk.kr/mcp/
```

---

## 가장 쉬운 길 — 링크 한 번 클릭

**[▶ Claude에 살길 추가하기](https://claude.ai/customize/connectors?modal=add-custom-connector&connectorName=%EC%82%B4%EA%B8%B8%20%EC%9E%AC%EB%82%9C%EB%8D%B0%EC%9D%B4%ED%84%B0&connectorUrl=https%3A%2F%2Fdatainfra.salgil.gyeongbuk.kr%2Fmcp%2F)**

이름과 주소가 채워진 추가 창이 열립니다. 확인하고 **추가**를 누르면 끝입니다.
터미널을 열 필요가 없고 **무료 플랜에서도 됩니다**(무료는 커스텀 커넥터 1개까지).

> 링크로 값이 채워지면 Claude가 **"외부 링크에서 온 주소이니 확인하라"는 안내**를 함께
> 띄웁니다. 정상입니다 — 링크는 입력란을 미리 채워줄 뿐이고, 추가 여부는 항상 본인이
> 확인해서 결정합니다. 주소가 `datainfra.salgil.gyeongbuk.kr`인지 보시면 됩니다.

로그인 화면은 나오지 않습니다 — 공개 데이터만 읽는 서버라 인증을 요구하지 않습니다.

직접 넣고 싶으면 **설정 → Connectors → 커스텀 커넥터 추가**에서 맨 위 주소를 붙여넣어도
같습니다.

한 번 추가하면 **claude.ai(웹), 데스크톱, 모바일 앱에서 전부** 쓸 수 있습니다. Anthropic 클라우드가 서버에 접속하는 구조라 어느 기기에서 켜든 동일하게 동작합니다.

> 회사 Team·Enterprise 플랜이면 **관리자(Owner)만** 커넥터를 추가할 수 있습니다. 관리자가 한 번 등록하면 구성원은 각자 **Connect**만 누르면 됩니다.

---

## ChatGPT — 클릭 한 번으로는 안 됩니다

**무료·Go 플랜은 커스텀 MCP를 지원하지 않습니다.** 유료 플랜에서 *개발자 모드*를 켜야 하고,
**웹 전용**(모바일 불가)입니다.

Claude 같은 프리필 링크는 없습니다. 커넥터 생성 창까지 열어주는 링크는 있지만
**주소는 직접 붙여넣어야** 하고, 개발자 모드가 이미 켜져 있어야 동작합니다.

1. **설정 → 보안 및 로그인 → 개발자 모드**를 켭니다
2. **[커넥터 생성 창 열기](https://chatgpt.com/plugins#settings/Connectors?create-connector=true&redirectAfter=%2Fplugins)** 를 누릅니다
3. 이름과 아래 주소를 넣습니다 — 인증(Authentication)은 **None**

```
https://datainfra.salgil.gyeongbuk.kr/mcp/
```

플랜별 제약이 공식 문서끼리도 엇갈립니다. **AI를 가볍게 쓰는 분께는 Claude 쪽을 권합니다.**

---

## 코딩 도구를 쓴다면

**Claude Code** — 한 줄입니다.

```bash
claude mcp add --transport http gbsafedata https://datainfra.salgil.gyeongbuk.kr/mcp/
```

**Cursor / VS Code** — 아래를 클릭하면 설정 창이 뜹니다.

- [Cursor에 추가](cursor://anysphere.cursor-deeplink/mcp/install?name=gbsafedata&config=eyJ1cmwiOiAiaHR0cHM6Ly9kYXRhaW5mcmEuc2FsZ2lsLmd5ZW9uZ2J1ay5rci9tY3AvIn0)
- [VS Code에 추가](vscode:mcp/install?%7B%22name%22%3A%20%22gbsafedata%22%2C%20%22type%22%3A%20%22http%22%2C%20%22url%22%3A%20%22https%3A//datainfra.salgil.gyeongbuk.kr/mcp/%22%7D)

**opencode** — `opencode.json`에 넣습니다. ([`plugins/opencode-remote.json`](../plugins/opencode-remote.json))

```json
{
  "mcp": {
    "gbsafedata": {
      "type": "remote",
      "url": "https://datainfra.salgil.gyeongbuk.kr/mcp/",
      "enabled": true
    }
  }
}
```

**Claude Desktop 설정파일을 직접 쓰는 경우** — 이 앱은 stdio만 말하므로 다리를 놓습니다. ([`plugins/claude-desktop-remote.json`](../plugins/claude-desktop-remote.json))

```json
{
  "mcpServers": {
    "gbsafedata": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://datainfra.salgil.gyeongbuk.kr/mcp/"]
    }
  }
}
```

> 위 설정 파일 방식은 앱 설정 화면에서 커넥터를 추가하는 것보다 번거롭습니다. 설정 화면에 Connectors 항목이 있다면 그쪽을 쓰세요.

끝의 슬래시(`/mcp/`)를 빼지 마세요. `/mcp`도 307로 넘겨주지만, POST에서 리다이렉트를 따라가지 않는 클라이언트가 있습니다.

---

## 붙였으면 이렇게 물어보세요

```
문경시 산사태 위험 확인해줘
지금 경북에 발효된 기상특보 있어?
안동시 대피소 어디 있어?
지진 났을 때 어디로 대피해야 하는지 알려줘
```

**제대로 붙었다면 이렇게 답합니다.** 마지막 질문에 "대피소 데이터가 없어 목적지를 안내할 수 없다"고 인정하고, 산사태 조회가 막혀 있으면 **"산사태 위험 없음"이 아니라 "산사태 자료를 읽지 못했다"** 고 구분해서 말합니다.

그 구분이 이 서버의 존재 이유입니다. 조회 실패를 위험 없음으로 답하는 것이 재난 데이터에서 가장 위험한 실패라서, 서버가 응답마다 "확인된 부재"인지 "확인 실패"인지를 표시하고 그 규칙을 모델에게 지침으로 함께 내려보냅니다.

---

## 안전에 관하여

- **읽기 전용입니다.** 12개 도구 전부 `readOnlyHint: true`, `destructiveHint: false`로 선언돼 있어, AI가 무언가를 바꾸거나 지우거나 전송할 수 없습니다.
- **개인정보를 다루지 않습니다.** 정부가 공개한 집계 데이터만 읽습니다.
- **여러분의 인증키가 필요 없습니다.** 서버가 정부 인증키를 들고 대신 호출합니다.
- **대피 결정을 내리지 않습니다.** 근거와 후보를 제시하고 판단은 사람이 합니다.

---

## 안 될 때

| 증상 | 확인할 것 |
| --- | --- |
| 커넥터 추가가 안 됨 | ChatGPT 무료·Go 플랜은 커스텀 MCP 미지원. Claude 무료는 커넥터 1개 제한 |
| 회사 계정에서 추가 버튼이 없음 | Team·Enterprise는 관리자만 추가 가능 |
| 도구가 안 뜸 | 주소 끝의 슬래시 확인 (`/mcp/`) |
| 산사태 답이 늘 비어 있음 | 정상입니다 — 해당 원천이 정부 심의 대기 중(403)이고, 답변이 그 사실을 밝힙니다 |

서버가 살아 있는지는 브라우저로 열어 확인할 수 있습니다:
[/v1/health](https://datainfra.salgil.gyeongbuk.kr/v1/health)

---

## 직접 돌리고 싶다면

레포를 받아 `./install.sh`를 실행하면 로컬 stdio 설정이 깔립니다. 다만 그 경우 정부 인증키를 직접 발급받아야 합니다. 대부분은 위의 원격 주소를 쓰는 편이 낫습니다.
