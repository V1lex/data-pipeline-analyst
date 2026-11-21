import copy

import pytest
from agent.analyst.formal_feedback import RevisionProposal
from agent.analyst.specification_builder import MockSpecificationBuilder
from agent.revision import validate_proposal
from pipeline_generator.errors import PipelineError


def test_rejects_assignments_already_required_by_the_same_guard():
    specification = MockSpecificationBuilder().build_verifiable_specification([])
    specification.formal_model = {
        "variables": [
            {"name": "stage", "type": "enum", "values": ["init", "done"]},
            {"name": "joined", "type": "boolean"},
        ],
        "initial": {"stage": "init", "joined": False},
        "transitions": [
            {
                "name": "write",
                "guard": {
                    "and": [
                        {"eq": [{"var": "stage"}, "init"]},
                        {"eq": [{"var": "joined"}, True]},
                    ]
                },
                "effects": [{"set": ["stage", "done"]}],
            }
        ],
        "properties": [],
    }
    transitions = copy.deepcopy(specification.formal_model["transitions"])
    transitions[0]["effects"].append({"set": ["joined", True]})
    proposal = RevisionProposal(
        issue_summary="x",
        proposed_change="x",
        rationale="x",
        patch_data={"transitions": transitions},
    )
    with pytest.raises(PipelineError, match="REVISION_INVALID"):
        validate_proposal(specification, proposal, 1)
