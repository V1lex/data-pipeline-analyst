import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline_spec.models import (
    AddExpr,
    AggregateDef,
    AggregateFunction,
    AggregateOperation,
    ColumnRef,
    ColumnSelection,
    DataConstraint,
    DataType,
    FileFormat,
    FilterOperation,
    JoinOperation,
    JoinType,
    LiteralVal,
    PipelineMetadata,
    PipelineOperation,
    PipelineSpecification,
    PipelineStep,
    ReadOperation,
    SaveMode,
    SelectOperation,
    SinkDataset,
    SortDirection,
    SortField,
    SortOperation,
    SourceDataset,
    WriteOperation,
)


def sample_valid_spec_dict() -> dict:
    return {
        "version": "1.0.0",
        "metadata": {
            "id": "simple_pipeline",
            "name": "Simple Pipeline",
            "description": "Reads source, filters rows and writes to sink",
        },
        "sources": [
            {
                "id": "src_users",
                "name": "Users",
                "format": "parquet",
                "location": "s3://bucket/users/",
            }
        ],
        "steps": [
            {
                "id": "step_read",
                "name": "Read Users",
                "inputs": ["src_users"],
                "operation": {"type": "read", "source_id": "src_users"},
            },
            {
                "id": "step_filter",
                "name": "Filter Active",
                "inputs": ["step_read"],
                "operation": {
                    "type": "filter",
                    "condition": {
                        "eq": [
                            {"col": "is_active"},
                            {"lit": True},
                        ]
                    },
                },
            },
            {
                "id": "step_write",
                "name": "Write Active Users",
                "inputs": ["step_filter"],
                "operation": {"type": "write", "sink_id": "sink_active_users"},
            },
        ],
        "sinks": [
            {
                "id": "sink_active_users",
                "name": "Active Users Mart",
                "from_step": "step_write",
                "format": "parquet",
                "location": "s3://bucket/active_users/",
            }
        ],
        "constraints": [
            {
                "id": "qc_user_id_not_null",
                "name": "User ID Not Null",
                "target_step": "step_filter",
                "columns": ["user_id"],
                "type": "not_null",
            }
        ],
    }


def test_valid_specification_parses():
    raw = sample_valid_spec_dict()
    spec = PipelineSpecification.model_validate(raw)

    assert spec.metadata.id == "simple_pipeline"
    assert len(spec.sources) == 1
    assert len(spec.steps) == 3
    assert len(spec.sinks) == 1
    assert spec.get_topological_order() == ["step_read", "step_filter", "step_write"]


def test_cyclic_dependency_raises_error():
    raw = sample_valid_spec_dict()
    # Introduce cycle: step_filter depends on step_write, but step_write depends on step_filter
    raw["steps"][1]["depends_on"] = ["step_write"]
    raw["steps"][2]["depends_on"] = ["step_filter"]

    with pytest.raises(ValidationError, match="Cyclic dependency detected"):
        PipelineSpecification.model_validate(raw)


def test_undefined_input_reference_raises_error():
    raw = sample_valid_spec_dict()
    raw["steps"][1]["inputs"] = ["non_existent_step"]

    with pytest.raises(ValidationError, match="references undefined input"):
        PipelineSpecification.model_validate(raw)


def test_undefined_sink_from_step_raises_error():
    raw = sample_valid_spec_dict()
    raw["sinks"][0]["from_step"] = "ghost_step"

    with pytest.raises(ValidationError, match="references undefined from_step"):
        PipelineSpecification.model_validate(raw)


def test_undefined_constraint_target_step_raises_error():
    raw = sample_valid_spec_dict()
    raw["constraints"][0]["target_step"] = "ghost_step"

    with pytest.raises(ValidationError, match="references undefined target_step"):
        PipelineSpecification.model_validate(raw)


def test_duplicate_step_ids_raises_error():
    raw = sample_valid_spec_dict()
    raw["steps"].append(raw["steps"][0].copy())

    with pytest.raises(ValidationError, match="Step IDs must be unique"):
        PipelineSpecification.model_validate(raw)


def test_example_ecommerce_json_validates():
    example_path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "e_commerce_orders_pipeline.json"
    )
    with open(example_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    spec = PipelineSpecification.model_validate(data)
    assert spec.metadata.id == "e_commerce_daily_sales_pipeline"
    assert len(spec.sources) == 2
    assert len(spec.steps) == 9
    assert len(spec.sinks) == 1
    assert len(spec.constraints) == 3
    assert spec.formal_model is not None

    # Check topological order can be resolved without error
    order = spec.get_topological_order()
    assert len(order) == 9
    assert order[-1] == "write_to_sales_mart"

    # Check formal_model conversion to formal_check input
    fc_input = spec.formal_model.to_formal_check_input()
    assert len(fc_input.variables) == 3
    assert len(fc_input.transitions) == 6
    assert len(fc_input.properties) == 4
