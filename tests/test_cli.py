"""CLI 테스트.

CLI에서 중요한 것은 출력 모양이 아니라 **종료 코드**다. 스크립트와 CI가 실패를
감지할 수 있어야 하고, 사용자에게 스택 트레이스가 보이면 안 된다.
"""

from __future__ import annotations

import json

import pytest
from gbsafe_cli.main import HazardChoice, OperationChoice, app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """네트워크를 쓰지 않는다. 원천 호출 없이 CLI 계약만 검증한다."""
    monkeypatch.setenv("GBSAFE_OFFLINE", "true")


class TestHelp:
    def test_root_help_lists_commands(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for command in ("doctor", "search", "verify", "region", "hazard", "quality"):
            assert command in result.output

    @pytest.mark.parametrize(
        "command",
        [
            "doctor", "search", "describe", "verify", "region", "hazard",
            "quality", "sources", "fetch", "normalize-csv", "serve", "mcp", "keys",
        ],
    )
    def test_every_command_has_help(self, command: str) -> None:
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0


class TestInvalidChoices:
    """오타가 조용히 통과하면 다른 재난의 데이터가 '완전'으로 표시된다."""

    def test_invalid_hazard_type_rejected(self) -> None:
        result = runner.invoke(app, ["hazard", "문경시", "--type", "invalid_type"])
        assert result.exit_code == 2
        assert "landslide" in result.output

    def test_invalid_operation_rejected(self) -> None:
        result = runner.invoke(app, ["verify", "15084084", "--operation", "bogus"])
        assert result.exit_code == 2
        assert "derive" in result.output

    def test_valid_choices_accepted(self) -> None:
        assert HazardChoice("landslide") is HazardChoice.LANDSLIDE
        assert OperationChoice("derive") is OperationChoice.DERIVE


class TestExitCodes:
    def test_unknown_dataset_is_nonzero(self) -> None:
        result = runner.invoke(app, ["describe", "99999999"])
        assert result.exit_code == 1

    def test_known_dataset_is_zero(self) -> None:
        result = runner.invoke(app, ["describe", "15084084"])
        assert result.exit_code == 0

    def test_unknown_region_is_nonzero(self) -> None:
        result = runner.invoke(app, ["region", "서울시"])
        assert result.exit_code == 1

    def test_transferred_region_reports_reason(self) -> None:
        result = runner.invoke(app, ["region", "군위군"])
        assert result.exit_code == 1
        assert "대구" in result.output

    def test_verify_blocked_operation_is_nonzero(self) -> None:
        """라이선스가 금지한 연산은 비정상 종료로 알린다."""
        result = runner.invoke(app, ["verify", "15073861", "--operation", "derive"])
        assert result.exit_code == 1

    def test_unknown_source_is_nonzero_and_lists_options(self) -> None:
        result = runner.invoke(app, ["fetch", "not_a_source"])
        assert result.exit_code == 2
        assert "weather_now" in result.output

    def test_missing_csv_file_is_nonzero(self) -> None:
        result = runner.invoke(app, ["normalize-csv", "shelters", "/nonexistent.csv"])
        assert result.exit_code == 2

    def test_directory_instead_of_file_is_nonzero(self, tmp_path) -> None:
        result = runner.invoke(app, ["normalize-csv", "shelters", str(tmp_path)])
        assert result.exit_code == 2

    def test_non_csv_source_is_nonzero(self, tmp_path) -> None:
        path = tmp_path / "x.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        result = runner.invoke(app, ["normalize-csv", "weather_now", str(path)])
        assert result.exit_code == 2

    def test_empty_csv_is_nonzero(self, tmp_path) -> None:
        """빈 파일이 성공으로 보고되면 자동화가 문제를 놓친다."""
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")
        result = runner.invoke(app, ["normalize-csv", "shelters", str(path)])
        assert result.exit_code == 1

    def test_header_only_csv_is_nonzero(self, tmp_path) -> None:
        path = tmp_path / "header.csv"
        path.write_text("시설명,위도,경도\n", encoding="utf-8")
        result = runner.invoke(app, ["normalize-csv", "shelters", str(path)])
        assert result.exit_code == 1

    def test_valid_csv_is_zero(self, tmp_path) -> None:
        path = tmp_path / "ok.csv"
        path.write_text(
            "시설명,소재지도로명주소,위도,경도,최대수용인원\n"
            "산북면사무소,경상북도 문경시 산북면,36.68,128.25,150\n",
            encoding="cp949",
        )
        result = runner.invoke(app, ["normalize-csv", "shelters", str(path)])
        assert result.exit_code == 0


class TestNoTracebackLeaks:
    """사용자에게 스택 트레이스를 보여주는 것은 버그다."""

    @pytest.mark.parametrize(
        "args",
        [
            ["fetch", "weather_now"],
            ["fetch", "weather_now", "--rows", "1"],
            ["hazard", "서울시"],
            ["hazard", "  "],
            ["describe", ""],
            ["region", ""],
            ["region", "../../etc/passwd"],
            ["search", "\x00null"],
            ["normalize-csv", "shelters", "/nonexistent"],
        ],
    )
    def test_no_traceback(self, args: list[str]) -> None:
        result = runner.invoke(app, args)
        assert "Traceback" not in result.output
        assert 'File "/' not in result.output


class TestJsonOutput:
    """스크립트가 쓸 수 있어야 한다."""

    @pytest.mark.parametrize(
        "args",
        [
            ["sources", "--json"],
            ["keys", "--json"],
            ["quality", "--json"],
            ["region", "문경시"],
            ["describe", "15084084"],
            ["verify", "15084084", "--json"],
            ["search", "산사태", "--json"],
            ["doctor", "--json"],
        ],
    )
    def test_output_is_parseable(self, args: list[str]) -> None:
        result = runner.invoke(app, args)
        json.loads(result.output)


class TestSecretsNotLeaked:
    def test_keys_command_hides_values(self) -> None:
        """발급 경로는 보여주고 키 값은 보여주지 않는다."""
        result = runner.invoke(app, ["keys", "--json"])
        payload = json.loads(result.output)
        for info in payload.values():
            assert set(info) == {"present", "env_var", "source"}
            assert isinstance(info["present"], bool)

    def test_doctor_hides_values(self) -> None:
        result = runner.invoke(app, ["doctor", "--json"])
        payload = json.loads(result.output)
        for info in payload["credentials"].values():
            assert set(info) == {"present", "source"}


class TestCiteCommand:
    def test_cite_succeeds(self) -> None:
        result = runner.invoke(app, ["cite", "15084084"])
        assert result.exit_code == 0
        assert "기상청" in result.output

    def test_cite_unknown_is_nonzero(self) -> None:
        assert runner.invoke(app, ["cite", "99999999"]).exit_code == 1

    def test_cite_json_parses(self) -> None:
        result = runner.invoke(app, ["cite", "15084084", "--json"])
        assert json.loads(result.output)["text"]
