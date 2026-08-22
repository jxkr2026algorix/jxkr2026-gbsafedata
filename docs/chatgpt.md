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

<img width="3334" height="2166" alt="CleanShot 2026-08-22 at 20 50 10@2x" src="https://github.com/user-attachments/assets/38c776d9-7383-4667-9bc5-9a4503197e48" />


---

## 2. 커넥터 만들기

[chatgpt.com/plugins](https://chatgpt.com/plugins)에서 **+** 를 누릅니다. 개발자 모드가 켜져 있어야 이 버튼이 커스텀 커넥터를 만듭니다.

아래 값을 넣습니다.

| 항목 | 값 |
| --- | --- |
| Name | `SALGIL \| 살길 – 재난데이터인프라` |
| Description | `경북 재난 공공데이터를 출처와 함께 조회합니다. 조회하지 못한 원천은 그 사실을 밝힙니다.` |
| MCP Server URL | `https://datainfra.salgil.gyeongbuk.kr/mcp/` |
| Authentication | **No Auth** |

주소 끝의 슬래시(`/mcp/`)를 빼지 마세요.

**Authentication은 반드시 No Auth(None)입니다.** 이 서버는 인증을 요구하지 않습니다 — 정부 인증키는 서버가 들고 있고, 공개된 집계 데이터만 읽습니다. OAuth나 토큰을 고르면 연결에 실패합니다.

<img width="3334" height="2166" alt="CleanShot 2026-08-22 at 20 53 45@2x" src="https://github.com/user-attachments/assets/a05c1cd8-5a07-41cf-835c-f17cf51688f8" />


---

## 3. 도구 스캔

**Create**를 누르면 ChatGPT가 서버에 접속해 도구 목록을 읽습니다. **12개**가 잡히면 정상입니다.

<img width="3334" height="2166" alt="CleanShot 2026-08-22 at 20 54 43@2x" src="https://github.com/user-attachments/assets/ac75ce7c-98ea-4406-b5bd-a51277231786" />


```
gbsafe_search_datasets      gbsafe_describe_dataset   gbsafe_verify_dataset
gbsafe_cite_dataset         gbsafe_resolve_region     gbsafe_hazard_context
gbsafe_hazard_capabilities  gbsafe_list_sources       gbsafe_fetch_source
gbsafe_data_health          gbsafe_quality_report     gbsafe_population_guidance
```

전부 읽기 전용으로 선언돼 있어(`readOnlyHint`) 쓰기 확인 모달이 뜨지 않습니다.

<img width="3334" height="2166" alt="CleanShot 2026-08-22 at 20 55 42@2x" src="https://github.com/user-attachments/assets/0cf903d8-12e9-44c6-9d82-1f01c46806f8" />


---

## 4. 대화에서 쓰기

작성창의 **+ → 개발자 모드**에서 이 커넥터를 켭니다. 대화마다 켜야 합니다.

<img width="3334" height="2166" alt="CleanShot 2026-08-22 at 20 57 10@2x" src="https://github.com/user-attachments/assets/065c23b7-98d0-407f-960c-93a143a40a8a" />


이렇게 물어보세요.

```
문경시 산사태 위험 확인해줘
지금 경북에 발효된 기상특보 있어?
안동시 지진 났을 때 어디로 대피해야 해?
```

**제대로 붙었다면** 마지막 질문에 대피소를 안내하지 못한다고 인정하고, 산사태 조회가 막혀 있으면 **"위험 없음"이 아니라 "자료를 읽지 못했다"** 고 구분해 답합니다.

<img width="2976" height="2996" alt="image" src="https://github.com/user-attachments/assets/3aad5013-f775-4ae9-a458-cf466fdfefaf" />

### 데이터가 있을 때는 이만큼 나옵니다

앞의 예시는 자료가 막혔을 때 어떻게 물러서는지를 보여준 것입니다. 실제로 벌어질 수 있는
상황을 물으면 이렇게 답합니다.

```
문경시에 지금 호우 상황 어때?
```

한 번의 질문으로 **기관 4곳을 동시에 조회**합니다 — 기상특보, 실황, 단기예보, 그리고
홍수통제소 수위관측소. 방금 실제 응답 기준으로 **레코드 168건에 인용 4건**이 붙어 나옵니다.

수위는 지점 단위로 옵니다.

```
문경시(비아교)  2.11 m   36.664, 128.263   2026-08-22 20:50 KST
```

여기까지는 다른 통합 API도 합니다. **차이는 답변에 따라붙는 11개의 단서입니다.**

- *발표관서 단위 특보입니다 — 관할 구역 전체가 대상이며 특정 마을 상태가 아닙니다*
- *문경시의 수위관측소 12곳 중 3곳(경천, 경천댐, 달지2)의 관측값이 이번 응답에 없습니다*
- *T/M 관측소 원시자료로 **보정 전 값**입니다 — 최종 확정자료와 다를 수 있습니다*
- *경북은 낙동강 권역이라 수집 지연이 11분 이상입니다 — 관측시각과 현재 시각의 차이를 그대로 읽으면 안 됩니다*
- *임계수위는 기관 고시값이며 실제 침수 여부는 현장 확인이 필요합니다*
- *예보값입니다 — 현재 관측 상황과 구별해야 합니다*

숫자 하나하나가 **어디까지 믿어도 되는지를 달고** 옵니다. 특보가 시·군 전체에 걸린 것인지
마을 상태인지, 12곳 중 9곳만 답한 것인지, 그 2.11 m가 확정값인지 보정 전 값인지,
11분 전 값을 지금으로 읽으면 안 되는지 — 전부 답변에 남습니다.

발효가 끝난 특보도 알아서 걷어냅니다. 이번 응답에서는 **타 지역 특보 89건을 제외**하고,
**발표 후 해제·대체된 통보문 9건**을 반영해 뺐습니다. 해제된 특보가 살아 있는 것처럼
보이면 그것도 잘못된 안심이기 때문입니다.

출처는 이렇게 따라옵니다.

```
기상청 「기상청 기상특보」 · 기준 2026-08-21T07:10:00+00:00 · KOGL-1
  · https://www.data.go.kr/data/15000415/openapi.do
```

**보고서에 그대로 붙일 수 있는 형태입니다** — 기관, 자료명, 기준 시각, 라이선스, 원본 주소.
어떤 값을 어디서 언제 가져왔는지 나중에 되짚을 수 있어야 재난 판단 근거가 됩니다.

이 경우 `complete: true`로 나갑니다. 조회 대상 4곳이 전부 답했다는 뜻이고, 앞의 산사태
사례에서 `complete: false`였던 것과 구별됩니다.

<img width="3334" height="2166" alt="CleanShot 2026-08-22 at 21 04 37@2x" src="https://github.com/user-attachments/assets/c67b0fbc-7ef3-4f8c-af7f-0b03b6e42185" />

<img width="3334" height="2166" alt="CleanShot 2026-08-22 at 21 04 45@2x" src="https://github.com/user-attachments/assets/60f160ff-94d3-40d1-91f9-70dba11136c1" />


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
