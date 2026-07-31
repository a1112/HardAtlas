import json
from pathlib import Path

from pydantic import Field

from .graph import (
    AgentDefinition,
    AgentGraphModel,
    AgentGraphSpec,
    AgentRegistry,
)


class AgentPackManifest(AgentGraphModel):
    id: str
    name: str
    version: str
    kernel_version: str
    agent_definition_files: list[str] = Field(default_factory=list)
    graph_files: list[str] = Field(default_factory=list)


class LoadedAgentPack(AgentGraphModel):
    root: str
    manifest: AgentPackManifest
    definitions: list[AgentDefinition]
    graphs: list[AgentGraphSpec]

    def registry(self) -> AgentRegistry:
        return AgentRegistry(self.definitions, self.graphs)


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_agent_pack(root: Path) -> LoadedAgentPack:
    manifest = AgentPackManifest.model_validate(_read_json(root / "manifest.json"))
    definitions = [
        AgentDefinition.model_validate(_read_json(root / relative))
        for relative in manifest.agent_definition_files
    ]
    graphs = [
        AgentGraphSpec.model_validate(_read_json(root / relative))
        for relative in manifest.graph_files
    ]
    registry = AgentRegistry(definitions, graphs)
    return LoadedAgentPack(
        root=str(root),
        manifest=manifest,
        definitions=list(registry.definitions.values()),
        graphs=list(registry.graphs.values()),
    )
