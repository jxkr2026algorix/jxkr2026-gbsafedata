# ChatGPT에 연결하기

ChatGPT는 Claude와 달리 **클릭 한 번으로 끝나지 않습니다.** 개발자 모드를 켜야 하고, 유료 플랜이어야 하며, 웹에서만 됩니다.

Claude를 쓰신다면 [connect.md](connect.md)의 링크 하나로 끝나니 그쪽을 권합니다.

---

## 먼저 확인할 것

| 항목 | 요구사항 |
| --- | --- |
| 플랜 | **무료·Go는 불가.** Plus 이상 (Business·Enterprise·Edu는 관리자 설정 필요) |
| 환경 | **웹 전용** — 모바일 앱에서는 커스텀 커넥터가 뜨지 않습니다 |
| 권한 | Business·Enterprise·Edu는 워크스페이스 관리자가 개발자 모드를 먼저 열어야 합니다 |

---

## 1. 개발자 모드 켜기

**설정 → 보안 및 로그인 → 개발자 모드**를 켭니다.

계정 종류에 따라 **설정 → 앱 및 커넥터 → 고급 설정**에 있기도 합니다. 토글이 아예 보이지 않으면 플랜이 지원하지 않거나 관리자가 막아둔 것입니다.

> **캡처 자리** — 개발자 모드 토글 화면
>
> ![개발자 모드 토글](images/chatgpt-01-developer-mode.png)

---

## 2. 커넥터 만들기

[chatgpt.com/plugins](https://chatgpt.com/plugins)에서 **+** 를 누릅니다. 개발자 모드가 켜져 있어야 이 버튼이 커스텀 커넥터를 만듭니다.

아래 값을 넣습니다.

| 항목 | 값 |
| --- | --- |
| Name | `SALGIL \| 살길 – 재난데이터인프라` |
| Description | `경북 재난 공공데이터를 출처와 함께 조회합니다. 조회하지 못한 원천은 그 사실을 밝힙니다.` |
| MCP Server URL | `https://datainfra.salgil.gyeongbuk.kr/mcp/` |
| Authentication | **None** |

주소 끝의 슬래시(`/mcp/`)를 빼지 마세요.

**Authentication은 반드시 None입니다.** 이 서버는 인증을 요구하지 않습니다 — 정부 인증키는 서버가 들고 있고, 공개된 집계 데이터만 읽습니다. OAuth나 토큰을 고르면 연결에 실패합니다.

> **캡처 자리** — 커넥터 생성 폼에 값을 채운 상태
>
> ![커넥터 생성 폼](images/chatgpt-02-create-connector.png)

---

## 3. 도구 스캔

**Create**를 누르면 ChatGPT가 서버에 접속해 도구 목록을 읽습니다. **12개**가 잡히면 정상입니다.

```
gbsafe_search_datasets      gbsafe_describe_dataset   gbsafe_verify_dataset
gbsafe_cite_dataset         gbsafe_resolve_region     gbsafe_hazard_context
gbsafe_hazard_capabilities  gbsafe_list_sources       gbsafe_fetch_source
gbsafe_data_health          gbsafe_quality_report     gbsafe_population_guidance
```

전부 읽기 전용으로 선언돼 있어(`readOnlyHint`) 쓰기 확인 모달이 뜨지 않습니다.

> **캡처 자리** — 스캔된 도구 12개 목록
>
> ![도구 목록](images/chatgpt-03-tools-scanned.png)

---

## 4. 대화에서 쓰기

작성창의 **+ → 개발자 모드**에서 이 커넥터를 켭니다. 대화마다 켜야 합니다.

> **캡처 자리** — 대화에서 커넥터를 켜는 메뉴
>
> ![커넥터 활성화](images/chatgpt-04-enable-in-chat.png)

이렇게 물어보세요.

```
문경시 산사태 위험 확인해줘
지금 경북에 발효된 기상특보 있어?
안동시 지진 났을 때 어디로 대피해야 해?
```

**제대로 붙었다면** 마지막 질문에 대피소를 안내하지 못한다고 인정하고, 산사태 조회가 막혀 있으면 **"위험 없음"이 아니라 "자료를 읽지 못했다"** 고 구분해 답합니다.

> **캡처 자리** — 조회 실패를 정직하게 보고하는 답변
>
> ![정직한 답변](images/chatgpt-05-honest-answer.png)

---

## 시스템 프롬프트를 함께 쓰세요

ChatGPT 커넥터는 서버의 `instructions`를 읽지만, 대화 지침으로 강하게 걸고 싶다면 아래를 커스텀 인스트럭션에 넣으시면 됩니다.

```
https://datainfra.salgil.gyeongbuk.kr/v1/agent/system-prompt
```

**도구만 붙이면 사고가 납니다.** 모델은 기본적으로 도움이 되려 하고, 산사태 조회가 403으로 실패해 결과가 비면 그냥 "산사태 위험 없습니다"라고 답합니다.

---

## 안 될 때

| 증상 | 원인 |
| --- | --- |
| 개발자 모드 토글이 없음 | 무료·Go 플랜이거나 관리자가 막아둠 |
| **+** 를 눌러도 커스텀 커넥터가 안 만들어짐 | 개발자 모드가 꺼져 있음 |
| 연결 실패 / 401 | Authentication을 None이 아닌 것으로 골랐음 |
| 도구가 0개로 스캔됨 | 주소 끝 슬래시 확인 (`/mcp/`) |
| 모바일에서 안 보임 | 커스텀 커넥터는 웹 전용 |
| 산사태 답이 늘 비어 있음 | 정상 — 해당 원천이 정부 심의 대기(403)이고, 답변이 그 사실을 밝힙니다 |

서버가 살아 있는지는 브라우저로 확인할 수 있습니다: [/v1/health](https://datainfra.salgil.gyeongbuk.kr/v1/health)

---

## 공개 디렉터리 등재는 별개입니다

위 방법은 **본인 계정에서만** 동작합니다. 아무나 클릭해서 쓰게 하려면 ChatGPT 플러그인 디렉터리에 제출해야 하고, 그건 법적 신원 검증 · 개인정보처리방침 · 이용약관 · 지원 페이지 · OpenAI 심사가 걸린 별도 절차입니다.

Claude는 그 과정 없이 [링크 하나](connect.md)로 배포됩니다.
