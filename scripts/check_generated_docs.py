#!/usr/bin/env python3
"""`docs/api.md`와 `docs/mcp.md`를 코드에서 생성한다.

두 문서는 실제 OpenAPI 스펙과 도구 정의에서 만들어진다. 손으로 고치면 즉시
구현과 어긋나므로 생성 결과를 커밋해 두고 CI가 최신 여부를 검사한다.

사용법:
    uv run python scripts/check_generated_docs.py          # 검사만 (CI)
    uv run python scripts/check_generated_docs.py --write  # 재생성
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DOC = REPO_ROOT / "docs" / "api.md"
MCP_DOC = REPO_ROOT / "docs" / "mcp.md"

ENVELOPE_ROWS = (
    ("`records[]`", "값 + 출처 + 신선도"),
    ("`records[].payload`", "정규화된 값"),
    ("`records[].source`", "기관·데이터셋·라이선스·시각·스냅샷·가공 허용 여부"),
    ("`records[].freshness`", "나이와 `usable_for_decision`"),
    ("`records[].quality_flags`", "확인된 결함 (좌표 누락, CP949 등)"),
    ("`citations[]`", "그대로 인용할 수 있는 완성된 문구"),
    ("`receipts[]`", "조회한 원천별 결과 — `records` / `confirmed_empty` / `failed`"),
    ("`degradations[]`", "조회하지 못한 원천과 사유"),
    ("`complete`", "`false`면 일부 원천 실패"),
    ("`absence_confirmed`", "`true`면 빈 결과를 '해당 없음'으로 읽어도 된다"),
    ("`caveats[]`", "해석 시 주의사항"),
    ("`modes[]`", "`real` / `snapshot` / `synthetic`"),
)

ABSENCE_ROWS = (
    ("`true`", "`true`", "조회 성공, 실제로 해당 사항이 없다"),
    (
        "`true`",
        "`false`",
        "원천이 '해당 없음'을 확인해 주지 않았다 — 위험 없음으로 읽으면 안 된다",
    ),
    ("`false`", "`false`", '일부 원천 조회 실패 — `receipts[].outcome == "failed"` 확인'),
)

MCP_RESPONSE_ROWS = (
    ("`records[]`", "값 + 출처 + 신선도"),
    ("`citations[]`", "답변에 그대로 인용할 문구"),
    ("`sources_checked[]`", "조회한 원천별 결과 (`records`/`confirmed_empty`/`failed`)"),
    ("`degradations[]`", "실패한 원천의 상태와 사유"),
    ("`complete`", "`false`면 일부 원천 조회 실패"),
    ("`absence_confirmed`", "`false`면 빈 결과를 '위험 없음'으로 답하면 안 된다"),
    ("`warnings[]`", "실패·오래된 자료·훈련 데이터·미확인 부재 경고"),
    ("`how_to_cite`", "인용 지침"),
)


def _table(header: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _enum_of(schema: dict[str, Any]) -> list[Any] | None:
    if schema.get("enum"):
        return schema["enum"]
    for variant in schema.get("anyOf", []):
        if isinstance(variant, dict) and variant.get("enum"):
            return variant["enum"]
    return None


def render_api_doc() -> str:
    from fastapi.testclient import TestClient
    from gbsafe_api.app import create_app

    spec = TestClient(create_app()).get("/openapi.json").json()

    lines = [
        "# 표준 API 명세",
        "",
        "`uv run gbsafe serve` 로 기동하고 `/docs`에서 대화형으로 확인할 수 있다.",
        "이 문서는 실제 OpenAPI 스펙에서 생성했다 —",
        "`uv run python scripts/check_generated_docs.py --write` 로 갱신한다.",
        "",
        "## 공통 응답 봉투",
        "",
        "데이터를 반환하는 엔드포인트는 모두 같은 구조를 쓴다.",
        "",
        *_table(("필드", "의미"), ENVELOPE_ROWS),
        "",
        "### 빈 결과를 읽는 방법",
        "",
        "`records`가 비었을 때 그 의미는 `absence_confirmed`가 결정한다.",
        "",
        *_table(("`complete`", "`absence_confirmed`", "의미"), ABSENCE_ROWS),
        "",
        "**`records`만 읽고 `absence_confirmed`를 무시하면 조회 실패가 '위험 없음'이 된다.**",
        "",
        "## 엔드포인트",
        "",
    ]

    for path, operations in sorted(spec["paths"].items()):
        for method, operation in operations.items():
            if operation.get("tags") is None and path == "/":
                continue
            lines += [f"### `{method.upper()} {path}`", ""]
            if operation.get("summary"):
                lines.append(operation["summary"])
            if operation.get("description"):
                lines += ["", operation["description"].strip()]

            parameters = operation.get("parameters", [])
            if parameters:
                rows = []
                for parameter in parameters:
                    description = parameter.get("description", "")
                    enum = _enum_of(parameter.get("schema", {}))
                    if enum:
                        joined = "` / `".join(str(value) for value in enum)
                        description = f"{description} (`{joined}`)".strip()
                    rows.append(
                        (
                            f"`{parameter['name']}`",
                            parameter["in"],
                            "예" if parameter.get("required") else "아니오",
                            description,
                        )
                    )
                lines += ["", *_table(("파라미터", "위치", "필수", "설명"), tuple(rows))]
            lines.append("")

    return "\n".join(lines) + "\n"


def render_mcp_doc() -> str:
    from gbsafe_mcp.tools import TOOLS

    lines = [
        "# MCP 도구 명세",
        "",
        "`uv run gbsafe-mcp` 로 기동한다. 설정은 [`plugins/`](../plugins)에 있다.",
        "이 문서는 실제 도구 정의에서 생성했다 —",
        "`uv run python scripts/check_generated_docs.py --write` 로 갱신한다.",
        "",
        f"**{len(TOOLS)}개 모두 읽기 전용이다.** 서버가 기동 시점에 도구 이름을 "
        "검사한다 — 조회 동사를",
        "포함하고 변경 동사가 없어야 통과한다.",
        "",
    ]

    for tool in TOOLS:
        lines += [f"## `{tool.name}`", "", f"**{tool.title}**", "", tool.description.strip(), ""]
        properties = tool.schema.get("properties", {})
        if properties:
            required = tool.schema.get("required", [])
            rows = []
            for name, schema in properties.items():
                description = schema.get("description", "")
                if schema.get("enum"):
                    joined = "` / `".join(schema["enum"])
                    description = f"{description} (`{joined}`)".strip()
                rows.append(
                    (
                        f"`{name}`",
                        schema.get("type", ""),
                        "예" if name in required else "아니오",
                        description,
                    )
                )
            lines += _table(("인자", "타입", "필수", "설명"), tuple(rows))
        else:
            lines.append("인자가 없다.")
        lines.append("")

    lines += [
        "## 응답 공통 필드",
        "",
        "데이터를 반환하는 도구는 다음을 함께 준다.",
        "",
        *_table(("필드", "의미"), MCP_RESPONSE_ROWS),
        "",
        "**`absence_confirmed`가 `false`이고 `records`가 비어 있으면 "
        "위험이 없다고 답하지 않는다.**",
        "무엇을 확인하지 못했는지 `sources_checked`에서 확인해 사용자에게 알린다.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    write = "--write" in sys.argv
    targets = ((API_DOC, render_api_doc()), (MCP_DOC, render_mcp_doc()))

    stale: list[Path] = []
    for path, rendered in targets:
        if write:
            path.write_text(rendered, encoding="utf-8")
            print(f"wrote {path.relative_to(REPO_ROOT)}")
            continue
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        if current != rendered:
            stale.append(path)

    if write:
        return 0
    if stale:
        names = ", ".join(str(path.relative_to(REPO_ROOT)) for path in stale)
        print(f"생성 문서가 구현과 다릅니다: {names}", file=sys.stderr)
        print(
            "uv run python scripts/check_generated_docs.py --write 로 갱신하고 커밋하세요.",
            file=sys.stderr,
        )
        return 1
    print("generated docs are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
