from pathlib import Path

from hardatlas_worker.tasks import build_agent_registry, build_parser_registry


def test_build_agent_registry_supports_glob_inputs() -> None:
    registry = build_agent_registry(
        f"{Path(__file__).resolve().parents[3] / 'agent-packs' / '*'}",
    )
    assert registry.graphs


def test_build_agent_registry_supports_absolute_directory_input() -> None:
    registry = build_agent_registry(
        str(Path(__file__).resolve().parents[3] / "agent-packs" / "core"),
    )
    assert registry.graphs


def test_build_agent_registry_rejects_unmatched_glob(tmp_path: Path) -> None:
    try:
        build_agent_registry(f"{tmp_path}/missing/*.json")
    except ValueError as error:
        assert "has no matches" in str(error)
    else:
        raise AssertionError("glob without matches must be rejected")


def test_build_agent_registry_rejects_non_directory_path(tmp_path: Path) -> None:
    manifest = tmp_path / "not-a-directory.json"
    manifest.write_text("{}")
    try:
        build_agent_registry(str(manifest))
    except ValueError as error:
        assert "must be a directory" in str(error)
    else:
        raise AssertionError("non-directory path must be rejected")


def test_build_agent_registry_rejects_empty_path_list() -> None:
    try:
        build_agent_registry("")
    except ValueError as error:
        assert "at least one agent pack path must be configured" in str(error)
    else:
        raise AssertionError("empty pack paths must be rejected")


def test_build_parser_registry_supports_glob_input(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.json").write_text(
        """
        {
            "id": "parser-worker-test",
            "version": "1.0.0",
            "format": "json",
            "mediaTypes": ["application/json"],
            "entityTypeId": "entity-type-plant",
            "locale": "zh-CN",
            "recordsPath": "/items",
            "externalIdPath": "id",
            "labelPath": "name",
            "fieldMappings": []
        }
        """.strip(),
        encoding="utf-8",
    )

    registry = build_parser_registry(f"{tmp_path}/*/*.json")
    assert registry.list()


def test_build_parser_registry_supports_glob_to_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.json").write_text(
        """
        {
            "id": "parser-worker-directory-glob",
            "version": "1.0.0",
            "format": "json",
            "mediaTypes": ["application/json"],
            "entityTypeId": "entity-type-plant",
            "locale": "zh-CN",
            "recordsPath": "/items",
            "externalIdPath": "id",
            "labelPath": "name",
            "fieldMappings": []
        }
        """.strip(),
        encoding="utf-8",
    )

    registry = build_parser_registry(f"{tmp_path}/*")
    assert registry.list()


def test_build_parser_registry_supports_absolute_glob_input(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.json").write_text(
        """
        {
            "id": "parser-worker-test-absolute",
            "version": "1.0.0",
            "format": "json",
            "mediaTypes": ["application/json"],
            "entityTypeId": "entity-type-plant",
            "locale": "zh-CN",
            "recordsPath": "/items",
            "externalIdPath": "id",
            "labelPath": "name",
            "fieldMappings": []
        }
        """.strip(),
        encoding="utf-8",
    )
    assert build_parser_registry(f"{nested}/*.json").list()


def test_build_parser_registry_supports_absolute_directory_input(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "deep.json").write_text(
        """
        {
            "id": "parser-worker-absolute-directory",
            "version": "1.0.0",
            "format": "json",
            "mediaTypes": ["application/json"],
            "entityTypeId": "entity-type-plant",
            "locale": "zh-CN",
            "recordsPath": "/items",
            "externalIdPath": "id",
            "labelPath": "name",
            "fieldMappings": []
        }
        """.strip(),
        encoding="utf-8",
    )
    assert build_parser_registry(str(nested)).list()


def test_build_parser_registry_rejects_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    try:
        build_parser_registry(str(tmp_path / "empty"))
    except ValueError as error:
        assert "no match" in str(error)
    else:
        raise AssertionError("empty parser directory must be rejected")


def test_build_parser_registry_rejects_unmatched_glob() -> None:
    try:
        build_parser_registry("missing/*.json")
    except ValueError as error:
        assert "no match" in str(error)
    else:
        raise AssertionError("glob without matches must be rejected")


def test_build_parser_registry_rejects_glob_to_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "empty-dir").mkdir()
    try:
        build_parser_registry(f"{tmp_path}/*-dir")
    except ValueError as error:
        assert "no match" in str(error)
    else:
        raise AssertionError("glob to empty directory must be rejected")


def test_build_parser_registry_rejects_empty_path_list() -> None:
    try:
        build_parser_registry("")
    except ValueError as error:
        assert "at least one extraction parser must be registered" in str(error)
    else:
        raise AssertionError("empty parser paths must be rejected")
