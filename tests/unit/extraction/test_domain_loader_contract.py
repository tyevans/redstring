"""Contract tests for the domain schema loader's failure paths.

Each refusal names the offending path or identifier. The read-failure test
simulates an OSError on one file, because a permission-based setup would not
fail when the suite runs as root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from redstring.extraction.domains.loader import (
    SchemaLoadError,
    get_schema_directory,
    load_all_schemas,
    load_schema_from_file,
)

_VALID_SCHEMA_TEXT = (get_schema_directory() / "literature_fiction.yaml").read_text(
    encoding="utf-8"
)


def test_a_read_failure_is_wrapped_naming_the_path_and_keeping_the_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "unreadable.yaml"
    target.write_text(_VALID_SCHEMA_TEXT, encoding="utf-8")
    original_read_text = Path.read_text

    def failing_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == target:
            raise PermissionError("simulated permission denied")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", failing_read_text)

    with pytest.raises(SchemaLoadError, match="Failed to read schema file") as info:
        load_schema_from_file(target)

    assert str(target) in str(info.value)
    assert info.value.file_path == target
    assert isinstance(info.value.cause, PermissionError)


def test_a_schema_directory_given_a_file_path_is_refused_naming_the_path(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "schema.yaml"
    not_a_dir.write_text(_VALID_SCHEMA_TEXT, encoding="utf-8")

    with pytest.raises(SchemaLoadError, match="Schema path is not a directory") as info:
        load_all_schemas(not_a_dir)

    assert info.value.file_path == not_a_dir


def test_two_files_with_one_domain_id_are_refused_naming_the_id(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(_VALID_SCHEMA_TEXT, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(_VALID_SCHEMA_TEXT, encoding="utf-8")

    with pytest.raises(SchemaLoadError, match="Duplicate domain_id 'literature_fiction'") as info:
        load_all_schemas(tmp_path)

    # Files are read in sorted order, so `a.yaml` wins and `b.yaml` is refused.
    assert info.value.file_path == tmp_path / "b.yaml"


def test_ignoring_errors_keeps_the_first_duplicate_and_drops_the_second(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(_VALID_SCHEMA_TEXT, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(_VALID_SCHEMA_TEXT, encoding="utf-8")

    schemas = load_all_schemas(tmp_path, ignore_errors=True)

    assert list(schemas) == ["literature_fiction"]
