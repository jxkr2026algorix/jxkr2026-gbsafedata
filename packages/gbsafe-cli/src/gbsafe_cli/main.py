"""GB SafeData CLI.

사람이 쓰는 표면이다. 특히 `doctor`가 중요하다 — "왜 이 데이터가 안 나오는가"는
이 프로젝트에서 가장 자주 묻게 되는 질문이고, 답이 인증키 부재·심의 대기·
포털 경유 필요 중 무엇인지 구별해 주지 않으면 사용자가 헛수고를 한다.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from gbsafe_api.envelope import envelope
from gbsafe_api.service import SafeDataService
from gbsafe_core.config import CREDENTIAL_SOURCES, CredentialName, get_settings
from gbsafe_mcp import run_stdio
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="gbsafe",
    help="경북 재난대피 공공데이터 — 검색·검증·인용·진단",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
_service = SafeDataService()

_OK = "[green]OK[/green]"
_BLOCKED = "[yellow]대기[/yellow]"
_FAIL = "[red]불가[/red]"


def _print_json(payload: Any) -> None:
    console.print_json(json.dumps(payload, ensure_ascii=False, default=str))


@app.command()
def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="JSON으로 출력")] = False,
) -> None:
    """원천 상태와 인증 정보를 진단한다.

    각 데이터 원천이 지금 쓸 수 있는지, 못 쓰면 왜인지 보여준다.
    """
    health = _service.data_health()
    if as_json:
        _print_json(health)
        return

    summary = health["summary"]
    catalog = summary["catalog"]
    console.print(
        Panel(
            f"카탈로그 [bold]{catalog['total']}[/bold]건 "
            f"(검증 {catalog['verified']}건, 결함 {catalog['with_defects']}건)\n"
            f"출처: {catalog['path']}\n"
            f"원천 [bold]{summary['available']}/{summary['connectors']}[/bold] 사용 가능"
            + ("  ·  오프라인 모드" if summary["offline_mode"] else ""),
            title="GB SafeData 상태",
            expand=False,
        )
    )

    table = Table(title="데이터 원천", show_lines=False)
    table.add_column("상태", width=6)
    table.add_column("이름", style="cyan")
    table.add_column("데이터셋")
    table.add_column("라이선스")
    table.add_column("비고", overflow="fold")

    for item in health["connectors"]:
        if item["available"]:
            state = _OK
        elif item["dev_review_required"]:
            state = _BLOCKED
        else:
            state = _FAIL
        table.add_row(
            state,
            item["name"],
            f"{item['dataset_id']} {item['dataset_name'][:22]}",
            item["license"],
            (item["reason"] or "")[:60],
        )
    console.print(table)

    creds = Table(title="인증 정보", show_lines=False)
    creds.add_column("보유", width=6)
    creds.add_column("이름", style="cyan")
    creds.add_column("발급 경로", overflow="fold")
    for name, info in health["credentials"].items():
        creds.add_row(_OK if info["present"] else "[dim]없음[/dim]", name, info["source"][:78])
    console.print(creds)

    missing = [name for name, info in health["credentials"].items() if not info["present"]]
    if missing:
        console.print(
            "\n[dim]키가 없는 원천은 조회 시 not_authorized로 보고되며 "
            "결과가 비어 있어도 '위험 없음'을 의미하지 않습니다.[/dim]"
        )


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="검색어 (예: 산사태 대피소)")] = "",
    hazard: Annotated[str | None, typer.Option(help="재난 유형")] = None,
    must_allow: Annotated[
        str | None,
        typer.Option(help="이 연산이 허용되는 것만 (read/derive/redistribute/commercial)"),
    ] = None,
    ready: Annotated[bool, typer.Option("--ready", help="지금 호출 가능한 것만")] = False,
    limit: Annotated[int, typer.Option(help="최대 개수")] = 15,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """공공데이터셋을 검색한다."""
    result = _service.search_datasets(
        query, hazard=hazard, dev_ready_only=ready, must_allow=must_allow, limit=limit
    )
    if as_json:
        _print_json(result)
        return

    table = Table(
        title=f"검색 결과 {result['count']}건 (지금 호출 가능 {result['callable_now']}건)"
    )
    table.add_column("ID", style="cyan", width=10)
    table.add_column("이름", overflow="fold")
    table.add_column("기관", width=14, overflow="ellipsis")
    table.add_column("라이선스", width=12)
    table.add_column("취득", width=16, overflow="ellipsis")
    table.add_column("착수", width=4)
    for item in result["datasets"]:
        table.add_row(
            item["dataset_id"],
            item["name"][:38],
            item["provider"],
            item["license"],
            item["how_to_obtain"],
            "[green]O[/green]" if item["dev_ready"] else "[yellow]-[/yellow]",
        )
    console.print(table)
    for note in result["notes"]:
        console.print(f"\n[yellow]![/yellow] {note}")


@app.command()
def describe(
    dataset_id: Annotated[str, typer.Argument(help="데이터셋 ID")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """데이터셋 상세 정보를 본다."""
    result = _service.describe_dataset(dataset_id)
    if as_json or not result.get("found"):
        _print_json(result)
        raise typer.Exit(0 if result.get("found") else 1)
    _print_json(result)


@app.command()
def verify(
    dataset_id: Annotated[str, typer.Argument(help="데이터셋 ID")],
    operation: Annotated[
        str, typer.Option(help="read | derive | redistribute | commercial")
    ] = "read",
) -> None:
    """이 데이터셋을 이 용도로 써도 되는지 판정한다."""
    result = _service.verify_dataset(dataset_id, operation)
    verdict = "[green]허용[/green]" if result.allowed else "[red]불가[/red]"
    console.print(
        Panel(
            f"{verdict}  ·  {result.dataset_name}\n"
            f"라이선스: {result.license_summary}\n"
            f"취득: {result.obtain_via}",
            title=f"{result.dataset_id} — {operation}",
            expand=False,
        )
    )
    for reason in result.reasons:
        console.print(f"  · {reason}")
    for warning in result.warnings:
        console.print(f"  [yellow]![/yellow] {warning}")
    raise typer.Exit(0 if result.allowed else 1)


@app.command()
def region(query: Annotated[str, typer.Argument(help="시군명 또는 코드")]) -> None:
    """지역명을 코드·좌표·기상격자로 변환한다."""
    result = _service.resolve_region(query)
    _print_json(result)
    raise typer.Exit(0 if result.get("found") else 1)


@app.command()
def hazard(
    region_name: Annotated[str, typer.Argument(help="경북 시군")],
    kind: Annotated[str, typer.Option("--type", help="재난 유형")] = "heavy_rain",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """특정 지역의 현재 위험 상황을 조회한다."""
    answer = asyncio.run(_service.hazard_context(region_name, hazard=kind))
    body = envelope(answer, {"region": region_name, "hazard": kind})
    if as_json:
        _print_json(body.model_dump(mode="json"))
        return

    status = "[green]완전[/green]" if body.complete else "[yellow]불완전[/yellow]"
    console.print(
        Panel(
            f"{status}  ·  레코드 {body.record_count}건  ·  출처 {len(body.citations)}건",
            title=f"{region_name} — {kind}",
            expand=False,
        )
    )
    for degradation in body.degradations:
        console.print(f"  [red]조회 실패[/red] {degradation.dataset_id}: {degradation.detail}")
    for caveat in body.caveats:
        console.print(f"  [yellow]![/yellow] {caveat}")
    if not body.complete:
        console.print(
            "\n[bold yellow]결과가 비어 있어도 '위험 없음'을 의미하지 않습니다.[/bold yellow]"
        )
    console.print("\n[bold]출처[/bold]")
    for citation in body.citations[:10]:
        console.print(f"  · {citation.text}")


@app.command()
def quality() -> None:
    """검증으로 확인된 데이터 품질 결함을 본다."""
    result = _service.quality_report()
    table = Table(title=f"확인된 결함 {result['count']}건")
    table.add_column("ID", style="cyan", width=10)
    table.add_column("이름", overflow="fold")
    table.add_column("결함")
    table.add_column("사용", width=5)
    for item in result["datasets"]:
        table.add_row(
            item["dataset_id"],
            item["name"][:34],
            ", ".join(item["flags"])[:40],
            "[green]가능[/green]" if item["usable_now"] else "[red]불가[/red]",
        )
    console.print(table)


@app.command()
def sources() -> None:
    """조회 가능한 데이터 원천 목록."""
    table = Table(title="데이터 원천")
    table.add_column("이름", style="cyan")
    table.add_column("데이터셋", width=10)
    table.add_column("설명", overflow="fold")
    for spec in _service.registry.all_specs():
        table.add_row(spec.name, spec.dataset_id, spec.summary)
    console.print(table)


@app.command()
def fetch(
    source: Annotated[str, typer.Argument(help="커넥터 이름 (sources 명령으로 확인)")],
    region_name: Annotated[str | None, typer.Option("--region")] = None,
    rows: Annotated[int, typer.Option()] = 20,
) -> None:
    """원천 하나를 직접 조회한다."""
    kwargs: dict[str, Any] = {"rows": rows}
    if region_name:
        if source in ("weather_now", "weather_forecast"):
            kwargs["location"] = region_name
        elif source == "emergency_beds":
            kwargs["sigungu"] = region_name
        else:
            kwargs["region"] = region_name
    answer = asyncio.run(_service.fetch_connector(source, **kwargs))
    _print_json(envelope(answer, {"source": source, **kwargs}).model_dump(mode="json"))


@app.command(name="normalize-csv")
def normalize_csv(
    source: Annotated[str, typer.Argument(help="shelters 또는 landslide_zones")],
    path: Annotated[Path, typer.Argument(help="포털에서 받은 CSV 경로")],
    region_name: Annotated[str | None, typer.Option("--region")] = None,
) -> None:
    """받아둔 파일데이터 CSV를 정규화한다.

    파일데이터는 세션 의존 때문에 자동 다운로드가 어려워 취득은 사용자가 한다.
    """
    if not path.is_file():
        console.print(f"[red]파일을 찾을 수 없습니다:[/red] {path}")
        raise typer.Exit(1)
    kwargs: dict[str, Any] = {}
    if region_name:
        kwargs["region"] = region_name
    answer = _service.normalize_csv(source, path, **kwargs)
    _print_json(envelope(answer, {"source": source, "file": str(path)}).model_dump(mode="json"))


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option("--reload")] = False,
) -> None:
    """표준 API 서버를 기동한다."""
    import uvicorn

    console.print(f"[green]API[/green] http://{host}:{port}/docs")
    console.print(
        "[dim]조회 전용입니다. 인증이 없으므로 공개 배치 시 "
        "앞단에 게이트웨이를 두세요.[/dim]"
    )
    uvicorn.run("gbsafe_api.app:app", host=host, port=port, reload=reload)


@app.command()
def mcp() -> None:
    """MCP 서버를 stdio로 기동한다."""
    asyncio.run(run_stdio())


@app.command()
def keys() -> None:
    """필요한 인증 정보와 발급 경로를 본다."""
    settings = get_settings()
    table = Table(title="인증 정보")
    table.add_column("보유", width=6)
    table.add_column("환경변수", style="cyan")
    table.add_column("발급 경로", overflow="fold")
    for name in CredentialName:
        table.add_row(
            _OK if settings.has(name) else "[dim]없음[/dim]",
            f"GBSAFE_{name.value.upper()}",
            CREDENTIAL_SOURCES[name],
        )
    console.print(table)


if __name__ == "__main__":
    app()
