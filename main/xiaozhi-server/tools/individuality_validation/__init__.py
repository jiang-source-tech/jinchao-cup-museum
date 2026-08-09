"""Offline gates for Xiaoxin individuality and longitudinal validation."""

from .checkpoint import CHECKPOINTS, evaluate_checkpoint_outcomes
from .contracts import GateCheck, GateReport, GateStatus
from .hil import (
    HILCaptureAttestation,
    HILEvent,
    HILIdentityBinding,
    HILLogRecord,
    HILManifest,
    evaluate_hil_bundle,
)
from .matrix import run_policy_matrix_gate
from .rollout import (
    ROLLOUT_STAGES,
    evaluate_legacy_cleanup,
    evaluate_rollout_transition,
)
from .research import (
    CounterfactualCandidate,
    CounterfactualPair,
    CounterfactualPolicyVariant,
    MemoryReference,
    MemoryTruthItem,
    StudyResponse,
    build_2afc_tasks,
    evaluate_research_results,
    freeze_policy_snapshot,
    generate_counterfactual_pair,
    validate_counterfactual_pair,
    validate_memory_references,
)

__all__ = (
    "CounterfactualCandidate",
    "CounterfactualPair",
    "CounterfactualPolicyVariant",
    "CHECKPOINTS",
    "GateCheck",
    "GateReport",
    "GateStatus",
    "HILEvent",
    "HILCaptureAttestation",
    "HILIdentityBinding",
    "HILLogRecord",
    "HILManifest",
    "MemoryReference",
    "MemoryTruthItem",
    "ROLLOUT_STAGES",
    "StudyResponse",
    "build_2afc_tasks",
    "evaluate_hil_bundle",
    "evaluate_checkpoint_outcomes",
    "evaluate_legacy_cleanup",
    "evaluate_research_results",
    "evaluate_rollout_transition",
    "freeze_policy_snapshot",
    "generate_counterfactual_pair",
    "run_policy_matrix_gate",
    "validate_counterfactual_pair",
    "validate_memory_references",
)
