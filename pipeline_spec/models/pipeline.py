from __future__ import annotations

from collections import defaultdict, deque

from pydantic import Field, model_validator

from pipeline_spec.models.base import SpecBaseModel
from pipeline_spec.models.constraints import DataConstraint
from pipeline_spec.models.formal_model import PipelineFormalModel
from pipeline_spec.models.operations import (
    JoinOperation,
    ReadOperation,
    UnionOperation,
    WriteOperation,
)
from pipeline_spec.models.sources_sinks import SinkDataset, SourceDataset
from pipeline_spec.models.steps import PipelineStep


class PipelineMetadata(SpecBaseModel):
    id: str = Field(min_length=1, description="Unique machine identifier for the pipeline")
    name: str = Field(min_length=1, description="Human-readable pipeline name")
    description: str | None = Field(default=None, description="Detailed explanation of pipeline goals")
    author: str | None = Field(default=None, description="Author or generating agent")
    tags: list[str] = Field(default_factory=list, description="Categorization tags")


class PipelineSpecification(SpecBaseModel):
    version: str = Field(default="1.0.0", description="Specification contract version (semver)")
    metadata: PipelineMetadata = Field(description="Pipeline metadata")
    sources: list[SourceDataset] = Field(default_factory=list, description="Input datasets")
    steps: list[PipelineStep] = Field(min_length=1, description="Transformation steps forming execution DAG")
    sinks: list[SinkDataset] = Field(default_factory=list, description="Output target datasets")
    constraints: list[DataConstraint] = Field(default_factory=list, description="Data quality & business rules")
    formal_model: PipelineFormalModel | None = Field(
        default=None,
        description="Formal verification model (states, transitions, properties) for TLA+/TLC",
    )

    @model_validator(mode="after")
    def validate_pipeline_contract(self) -> PipelineSpecification:
        self._validate_unique_ids()
        self._validate_references()
        self._validate_dag_acyclicity()
        return self

    def _validate_unique_ids(self) -> None:
        source_ids = [s.id for s in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError(f"Source IDs must be unique: {source_ids}")

        sink_ids = [s.id for s in self.sinks]
        if len(sink_ids) != len(set(sink_ids)):
            raise ValueError(f"Sink IDs must be unique: {sink_ids}")

        step_ids = [s.id for s in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError(f"Step IDs must be unique: {step_ids}")

        constraint_ids = [c.id for c in self.constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError(f"Constraint IDs must be unique: {constraint_ids}")

        # Collision between sources and steps
        collision = set(source_ids).intersection(set(step_ids))
        if collision:
            raise ValueError(f"Source ID and Step ID collide: {collision}")

    def _validate_references(self) -> None:
        source_ids = {s.id for s in self.sources}
        sink_ids = {s.id for s in self.sinks}
        step_ids = {s.id for s in self.steps}
        all_available_inputs = source_ids.union(step_ids)

        # Validate step inputs and operations
        for step in self.steps:
            for inp in step.inputs:
                if inp not in all_available_inputs:
                    raise ValueError(
                        f"Step '{step.id}' references undefined input '{inp}'. "
                        f"Available inputs: {sorted(all_available_inputs)}"
                    )

            for dep in step.depends_on:
                if dep not in all_available_inputs:
                    raise ValueError(
                        f"Step '{step.id}' depends on undefined step/source '{dep}'."
                    )

            op = step.operation
            if isinstance(op, ReadOperation):
                if op.source_id not in source_ids:
                    raise ValueError(
                        f"Read operation in step '{step.id}' references undefined source '{op.source_id}'."
                    )
            elif isinstance(op, JoinOperation):
                if op.right_input not in all_available_inputs:
                    raise ValueError(
                        f"Join operation in step '{step.id}' references undefined right input '{op.right_input}'."
                    )
            elif isinstance(op, UnionOperation):
                for other in op.other_inputs:
                    if other not in all_available_inputs:
                        raise ValueError(
                            f"Union operation in step '{step.id}' references undefined input '{other}'."
                        )
            elif isinstance(op, WriteOperation):
                if op.sink_id not in sink_ids:
                    raise ValueError(
                        f"Write operation in step '{step.id}' references undefined sink '{op.sink_id}'."
                    )

        # Validate sink source references
        for sink in self.sinks:
            if sink.from_step not in step_ids:
                raise ValueError(
                    f"Sink '{sink.id}' references undefined from_step '{sink.from_step}'."
                )

        # Validate constraint target steps
        for constraint in self.constraints:
            if constraint.target_step not in step_ids:
                raise ValueError(
                    f"Constraint '{constraint.id}' references undefined target_step '{constraint.target_step}'."
                )

    def _validate_dag_acyclicity(self) -> None:
        """Validates that step dependencies form a Directed Acyclic Graph (DAG) using Kahn's algorithm."""
        self.get_topological_order()

    def get_topological_order(self) -> list[str]:
        """Returns step IDs in topologically sorted execution order.
        Raises ValueError if a cycle is detected.
        """
        step_ids = {s.id for s in self.steps}
        in_degree: dict[str, int] = {s.id: 0 for s in self.steps}
        adjacency: dict[str, list[str]] = defaultdict(list)

        for step in self.steps:
            # We consider dependencies on other steps
            dependencies = {dep for dep in step.depends_on if dep in step_ids}
            for dep in dependencies:
                adjacency[dep].append(step.id)
                in_degree[step.id] += 1

        queue = deque([sid for sid, deg in in_degree.items() if deg == 0])
        ordered: list[str] = []

        while queue:
            current = queue.popleft()
            ordered.append(current)
            for neighbor in adjacency[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(ordered) != len(self.steps):
            cycle_nodes = [sid for sid, deg in in_degree.items() if deg > 0]
            raise ValueError(f"Cyclic dependency detected among steps: {cycle_nodes}")

        return ordered
