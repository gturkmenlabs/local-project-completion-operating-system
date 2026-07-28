"""Project intelligence layer: what this repository is *for*.

The task graph answers "what is unfinished". This layer answers "unfinished
towards what" — the question a human asks before touching any code.
"""

from .project_model import (
    MODEL_FILENAME,
    ProjectModel,
    ProjectModelBuilder,
    load_model,
    model_path,
    save_model,
)

__all__ = [
    "MODEL_FILENAME",
    "ProjectModel",
    "ProjectModelBuilder",
    "load_model",
    "model_path",
    "save_model",
]
