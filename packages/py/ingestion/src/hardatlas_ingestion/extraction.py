import csv
import hashlib
import glob
import io
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from hardatlas_domain import (
    Citation,
    ExtractionBatch,
    ExtractionCandidate,
    ExtractionFieldCandidate,
    ExtractionLocator,
    ExtractionParserDefinition,
    LocalizedText,
    SourceDefinition,
    SourceSnapshot,
)


class ExtractionError(ValueError):
    pass


def _digest(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _canonical_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ExtractionError("snapshot is not valid UTF-8") from error


def _path_tokens(path: str) -> list[str]:
    if path in {"", "/"}:
        return []
    if path.startswith("/"):
        return [
            token.replace("~1", "/").replace("~0", "~")
            for token in path.removeprefix("/").split("/")
        ]
    return path.split(".")


def _read_path(record: Any, path: str) -> Any:
    current = record
    for token in _path_tokens(path):
        if isinstance(current, dict):
            if token not in current:
                raise KeyError(path)
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            if index >= len(current):
                raise KeyError(path)
            current = current[index]
        else:
            raise KeyError(path)
    return current


@dataclass
class _HtmlElement:
    tag: str
    attributes: dict[str, str]
    parent: "_HtmlElement | None" = None
    children: list["_HtmlElement"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [*self.text_parts]
        for child in self.children:
            parts.append(child.text())
        return " ".join(" ".join(parts).split())

    def descendants(self) -> Iterable["_HtmlElement"]:
        for child in self.children:
            yield child
            yield from child.descendants()


class _RestrictedHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HtmlElement(tag="document", attributes={})
        self.current = self.root
        self._ignored_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in {"script", "style", "template"}:
            self._ignored_depth += 1
        element = _HtmlElement(
            tag=tag.casefold(),
            attributes={key.casefold(): value or "" for key, value in attrs},
            parent=self.current,
        )
        self.current.children.append(element)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta"}:
            self.current = element

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if self.current.tag == tag and self.current.parent is not None:
            self.current = self.current.parent

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template"} and self._ignored_depth:
            self._ignored_depth -= 1
        cursor = self.current
        while cursor.parent is not None:
            if cursor.tag == tag.casefold():
                self.current = cursor.parent
                return
            cursor = cursor.parent

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.current.text_parts.append(data)


def _matches_selector(element: _HtmlElement, selector: str) -> bool:
    selector = selector.strip()
    if not selector or any(character in selector for character in (" ", ">", "+", "~", ",")):
        raise ExtractionError("HTML selectors support only one restricted simple selector")
    attribute_name: str | None = None
    attribute_value: str | None = None
    if "[" in selector:
        if not selector.endswith("]") or selector.count("[") != 1:
            raise ExtractionError("invalid restricted HTML attribute selector")
        selector, attribute = selector[:-1].split("[", 1)
        if "=" in attribute:
            attribute_name, attribute_value = attribute.split("=", 1)
            attribute_value = attribute_value.strip("\"'")
        else:
            attribute_name = attribute
        attribute_name = attribute_name.casefold().strip()
    identifier: str | None = None
    class_name: str | None = None
    tag = selector
    if "#" in selector:
        tag, identifier = selector.split("#", 1)
    elif "." in selector:
        tag, class_name = selector.split(".", 1)
    if tag and element.tag != tag.casefold():
        return False
    if identifier is not None and element.attributes.get("id") != identifier:
        return False
    if class_name is not None:
        classes = element.attributes.get("class", "").split()
        if class_name not in classes:
            return False
    if attribute_name is not None:
        if attribute_name not in element.attributes:
            return False
        if (
            attribute_value is not None
            and element.attributes[attribute_name] != attribute_value
        ):
            return False
    return True


def _select(root: _HtmlElement, selector: str) -> list[_HtmlElement]:
    candidates = [root, *root.descendants()]
    return [element for element in candidates if _matches_selector(element, selector)]


def _read_html_path(record: _HtmlElement, path: str) -> Any:
    selector, separator, attribute = path.rpartition("@")
    if not separator:
        selector = path
        attribute = ""
    if selector:
        matches = _select(record, selector)
        if not matches:
            raise KeyError(path)
        element = matches[0]
    else:
        element = record
    if attribute:
        if attribute.casefold() not in element.attributes:
            raise KeyError(path)
        return element.attributes[attribute.casefold()]
    return element.text()


class ParserRegistry:
    def __init__(
        self,
        definitions: Iterable[ExtractionParserDefinition] = (),
    ) -> None:
        self._definitions: dict[tuple[str, str], ExtractionParserDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ExtractionParserDefinition) -> None:
        key = (definition.id, definition.version)
        current = self._definitions.get(key)
        if current is not None and current != definition:
            raise ExtractionError("parser definition versions are immutable")
        self._definitions[key] = definition

    def get(self, parser_id: str, parser_version: str) -> ExtractionParserDefinition:
        definition = self._definitions.get((parser_id, parser_version))
        if definition is None:
            raise ExtractionError(
                f"parser definition is unavailable: {parser_id}@{parser_version}"
            )
        return definition

    def resolve_for_source(self, source: SourceDefinition) -> ExtractionParserDefinition:
        if source.parser_id is None or source.parser_version is None:
            raise ExtractionError("source has no pinned parser id and version")
        definition = self.get(source.parser_id, source.parser_version)
        source_media_types = {
            media_type.casefold() for media_type in source.allowed_media_types
        }
        if not source_media_types.intersection(definition.media_types):
            raise ExtractionError("source and parser have no compatible media type")
        return definition

    def list(self) -> list[ExtractionParserDefinition]:
        return sorted(
            self._definitions.values(),
            key=lambda item: (item.id, item.version),
        )


def load_parser_registry(configured_paths: str) -> ParserRegistry:
    registry = ParserRegistry()
    for configured_path in configured_paths.split(","):
        value = configured_path.strip()
        if not value:
            continue
        is_glob = any(item in value for item in ("*", "?", "["))
        path = Path(value)
        if not path.is_absolute():
            path = Path.cwd() / path

        if is_glob:
            paths = sorted(glob.glob(value, recursive=True))
            if not paths:
                raise ExtractionError(f"parser definition path has no match: {value}")
            discovered: list[Path] = [Path(item) for item in paths]
        else:
            discovered = [path]

        files: list[Path] = []
        for candidate in discovered:
            if not candidate.exists():
                raise ExtractionError(f"parser definition path does not exist: {candidate}")
            if candidate.is_dir():
                json_files = sorted(candidate.rglob("*.json"))
                if not json_files:
                    raise ExtractionError(
                        f"parser definition path has no match: {candidate}"
                    )
                files.extend(json_files)
            else:
                files.append(candidate)

        if not files and is_glob:
            raise ExtractionError(f"parser definition path has no match: {value}")

        for file in files:
            if not file.is_file():
                raise ExtractionError(f"parser definition path is not a file: {file}")
            if file.suffix.lower() != ".json":
                raise ExtractionError(f"parser definition must use .json extension: {file}")
            try:
                document = json.loads(file.read_text(encoding="utf-8"))
                registry.register(ExtractionParserDefinition.model_validate(document))
            except (OSError, json.JSONDecodeError, ValueError) as error:
                raise ExtractionError(f"invalid parser definition {file}: {error}") from error
    return registry


def _structured_records(
    definition: ExtractionParserDefinition,
    content: bytes,
) -> list[tuple[Any, ExtractionLocator]]:
    text = _decode(content)
    if definition.format == "json":
        try:
            document = json.loads(text)
            records = _read_path(document, definition.records_path or "")
        except (json.JSONDecodeError, KeyError) as error:
            raise ExtractionError(f"invalid JSON snapshot: {error}") from error
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            raise ExtractionError("JSON records path must resolve to an object or array")
        base = definition.records_path or "/"
        return [
            (
                record,
                ExtractionLocator(kind="json-pointer", value=f"{base.rstrip('/')}/{index}"),
            )
            for index, record in enumerate(records)
        ]
    if definition.format == "jsonl":
        result: list[tuple[Any, ExtractionLocator]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ExtractionError(f"invalid JSONL at line {line_number}: {error}") from error
            result.append(
                (
                    record,
                    ExtractionLocator(kind="jsonl-line", value=str(line_number)),
                )
            )
        return result
    if definition.format == "csv":
        try:
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames:
                raise ExtractionError("CSV snapshot has no header")
            return [
                (
                    dict(record),
                    ExtractionLocator(kind="csv-row", value=str(row_number)),
                )
                for row_number, record in enumerate(reader, start=2)
            ]
        except csv.Error as error:
            raise ExtractionError(f"invalid CSV snapshot: {error}") from error
    parser = _RestrictedHtmlParser()
    try:
        parser.feed(text)
        parser.close()
    except ValueError as error:
        raise ExtractionError(f"invalid HTML snapshot: {error}") from error
    assert definition.record_selector is not None
    return [
        (
            element,
            ExtractionLocator(
                kind="html-selector",
                value=f"{definition.record_selector}:nth-record({index + 1})",
            ),
        )
        for index, element in enumerate(_select(parser.root, definition.record_selector))
    ]


def extract_snapshot(
    definition: ExtractionParserDefinition,
    *,
    source: SourceDefinition,
    snapshot: SourceSnapshot,
    content: bytes,
) -> tuple[ExtractionBatch, list[ExtractionCandidate]]:
    if snapshot.source_id != source.id or snapshot.source_version != source.version:
        raise ExtractionError("snapshot does not belong to the pinned source version")
    if snapshot.media_type.casefold() not in definition.media_types:
        raise ExtractionError("snapshot media type is not accepted by the parser")
    if len(content) != snapshot.byte_size:
        raise ExtractionError("snapshot byte size does not match immutable metadata")
    if _digest(content) != snapshot.content_sha256:
        raise ExtractionError("snapshot content hash does not match immutable metadata")
    if source.parser_id != definition.id or source.parser_version != definition.version:
        raise ExtractionError("source is not pinned to this parser version")

    records = _structured_records(definition, content)
    if len(records) > definition.max_records:
        raise ExtractionError(
            f"parser record limit exceeded: {len(records)} > {definition.max_records}"
        )
    batch_id = f"extraction-{_digest(f'{snapshot.id}:{definition.id}:{definition.version}')[:24]}"
    candidates: list[ExtractionCandidate] = []
    read_value = _read_html_path if definition.format == "html" else _read_path
    for ordinal, (record, record_locator) in enumerate(records):
        try:
            label_value = read_value(record, definition.label_path)
        except KeyError as error:
            raise ExtractionError(
                f"required label path is missing at {record_locator.value}"
            ) from error
        label = str(label_value).strip()
        if not label:
            raise ExtractionError(f"empty label at {record_locator.value}")

        def optional_value(
            path: str | None,
            current_record: Any = record,
        ) -> str | None:
            if not path:
                return None
            try:
                value = read_value(current_record, path)
            except KeyError:
                return None
            normalized = str(value).strip()
            return normalized or None

        external_id = optional_value(definition.external_id_path)
        entity_id = optional_value(definition.entity_id_path)
        stable_record_key = external_id or f"{ordinal}:{label}"
        candidate_hash = _digest(f"{batch_id}:{stable_record_key}")
        candidate_id = f"candidate-{candidate_hash[:24]}"
        citation_id = f"citation-{candidate_hash[:24]}"
        field_candidates: list[ExtractionFieldCandidate] = []
        for mapping_index, mapping in enumerate(definition.field_mappings):
            try:
                value = read_value(record, mapping.source_path)
            except KeyError as error:
                if mapping.required:
                    raise ExtractionError(
                        f"required field {mapping.source_path} is missing "
                        f"at {record_locator.value}"
                    ) from error
                continue
            field_hash = _digest(
                f"{candidate_id}:{mapping_index}:{mapping.target_path}:{_canonical_value(value)}"
            )
            locator_value = f"{record_locator.value}::{mapping.source_path}"
            field_candidates.append(
                ExtractionFieldCandidate(
                    id=f"field-{field_hash[:24]}",
                    target_path=mapping.target_path,
                    attribute_definition_id=mapping.attribute_definition_id,
                    original_value=value,
                    proposed_value=value,
                    locale=mapping.locale,
                    confidence=mapping.confidence,
                    locator=ExtractionLocator(
                        kind=record_locator.kind,
                        value=locator_value,
                    ),
                    quote_hash=_digest(_canonical_value(value)),
                    citation_id=citation_id,
                )
            )
        citation = Citation(
            id=citation_id,
            source_id=source.id,
            source_title=source.name,
            source_url=snapshot.url,
            source_tier=source.trust_tier,
            retrieved_at=snapshot.retrieved_at,
            locator=record_locator.value,
            quote_hash=_digest(
                _canonical_value(
                    record.text() if isinstance(record, _HtmlElement) else record
                )
            ),
        )
        candidates.append(
            ExtractionCandidate(
                id=candidate_id,
                batch_id=batch_id,
                snapshot_id=snapshot.id,
                source_id=source.id,
                source_version=source.version,
                parser_id=definition.id,
                parser_version=definition.version,
                entity_type_id=definition.entity_type_id,
                entity_id=entity_id,
                external_id=external_id,
                labels=[
                    LocalizedText(
                        locale=definition.locale,
                        value=label,
                    )
                ],
                fields=field_candidates,
                citation=citation,
                record_locator=record_locator,
                created_at=snapshot.retrieved_at,
            )
        )
    batch = ExtractionBatch(
        id=batch_id,
        snapshot_id=snapshot.id,
        source_id=source.id,
        source_version=source.version,
        content_sha256=snapshot.content_sha256,
        parser_id=definition.id,
        parser_version=definition.version,
        status="completed",
        candidate_ids=[candidate.id for candidate in candidates],
        candidate_count=len(candidates),
        created_at=snapshot.retrieved_at,
    )
    return batch, candidates
