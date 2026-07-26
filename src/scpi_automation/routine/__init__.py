"""Pure routine-composition models and conceptual feature catalog.

This package deliberately contains no VISA session, SCPI command, or execution
logic. A model-specific profile must later translate a verified feature into an
instrument command.
"""

from .catalog import (
    feature_by_id,
    features_for,
    local_extension_features_for,
    select_feature,
)
from .models import (
    DelayStep,
    FeatureRisk,
    FeatureVerification,
    PlanArgumentBinding,
    PlanBoundDelayStep,
    RoutineStep,
    RoutineFeature,
    RoutineParameter,
    SelectedFeature,
    SelectedInstrument,
    WaitForCompletionStep,
    create_delay,
    create_plan_bound_delay,
    wait_for_completion,
)
from .storage import (
    SCHEMA_VERSION,
    RoutineFile,
    RoutineStorageError,
    load_routine,
    load_routine_requirements,
    save_routine,
)

__all__ = [
    "DelayStep",
    "FeatureRisk",
    "FeatureVerification",
    "PlanArgumentBinding",
    "PlanBoundDelayStep",
    "RoutineStep",
    "RoutineFeature",
    "RoutineParameter",
    "RoutineFile",
    "RoutineStorageError",
    "SCHEMA_VERSION",
    "SelectedFeature",
    "SelectedInstrument",
    "WaitForCompletionStep",
    "create_delay",
    "create_plan_bound_delay",
    "feature_by_id",
    "features_for",
    "load_routine",
    "load_routine_requirements",
    "local_extension_features_for",
    "save_routine",
    "select_feature",
    "wait_for_completion",
]
