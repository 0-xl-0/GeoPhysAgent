from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuleDefinition:
    rule_id: str
    version: str
    title: str
    source: str
    parameters: dict[str, Any] = field(default_factory=dict)
    applicability: str = ""
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RulePack:
    pack_id: str
    version: str
    title: str
    source: str
    rules: tuple[RuleDefinition, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def definition(self, rule_id: str) -> RuleDefinition:
        selected = next((item for item in self.rules if item.rule_id == rule_id), None)
        if selected is None:
            raise KeyError(f"Rule is not present in pack {self.pack_id}@{self.version}: {rule_id}")
        return selected


@dataclass(frozen=True)
class FigureAxis:
    axis_id: str
    label: str
    unit: str
    minimum: float
    maximum: float
    direction: str = "increasing"
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FigureArtifactMetadata:
    figure_id: str
    figure_type: str
    title: str
    tool_name: str
    tool_version: str
    source_result_ids: tuple[str, ...]
    evidence_references: tuple[str, ...]
    axes: tuple[FigureAxis, ...]
    series: tuple[dict[str, Any], ...]
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.figure_id or not self.figure_type or not self.tool_name:
            raise ValueError("Figure metadata requires stable figure/type/tool identities")
        if not self.source_result_ids or not self.evidence_references or not self.axes:
            raise ValueError("Figure metadata requires sources, Evidence and axes")
        for axis in self.axes:
            if axis.minimum > axis.maximum:
                raise ValueError(f"Figure axis range is invalid: {axis.axis_id}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReportSemanticContract:
    """Domain-neutral separation required in a professional reference report."""

    sections: tuple[str, ...] = (
        "观测事实",
        "确定性规则判断",
        "模型解释",
        "假设",
        "未解决问题",
        "能力边界",
    )
    evidence_required_for_facts: bool = True
    model_interpretation_is_evidence: bool = False
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
