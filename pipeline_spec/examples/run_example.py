#!/usr/bin/env python3
"""Example runner demonstrating loading, validating, and compiling the pipeline specification."""

import json
from pathlib import Path

from pipeline_spec.models import PipelineSpecification


def main() -> None:
    example_path = Path(__file__).parent / "e_commerce_orders_pipeline.json"
    print(f"Loading pipeline specification from: {example_path}")

    with open(example_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    # 1. Pydantic validation of unified contract
    spec = PipelineSpecification.model_validate(raw_data)
    print(f"✓ Pipeline '{spec.metadata.name}' (ID: {spec.metadata.id}) is valid!")

    # 2. DAG Topological Sort
    execution_order = spec.get_topological_order()
    print("\n✓ Resolved DAG execution order for PySpark generator:")
    for idx, step_id in enumerate(execution_order, start=1):
        step = next(s for s in spec.steps if s.id == step_id)
        print(f"  {idx}. [{step.operation.type.upper()}] {step.id} -> '{step.name}'")

    # 3. Formal Model inspection
    if spec.formal_model:
        fc_input = spec.formal_model.to_formal_check_input()
        print("\n✓ Formal verification model extracted successfully:")
        print(f"  - Variables: {[v.name for v in fc_input.variables]}")
        print(f"  - Transitions: {[t.name for t in fc_input.transitions]}")
        print(f"  - Properties: {[p.id for p in fc_input.properties]}")

    print("\n✓ Specification is ready for verification (TLA+/TLC) and PySpark code generation!")


if __name__ == "__main__":
    main()
