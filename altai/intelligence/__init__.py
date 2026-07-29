"""Project intelligence layer: what this repository is *for*.

The task graph answers "what is unfinished". This layer answers "unfinished
towards what" (:mod:`.project_model`), "where does that answer contradict the
repository" (:mod:`.gap_analyzer`), "which files does a task likely touch"
(:mod:`.code_graph`), "what has this project already learned about itself"
(:mod:`.project_memory`), and "what might be worth doing that nobody asked for
by name" (:mod:`.opportunity_finder`) — the questions a human asks before
touching any code.
"""

from .code_graph import (
    GRAPH_FILENAME,
    CodeGraph,
    Symbol,
    build_code_graph,
    graph_path,
    load_graph,
    related_files,
    save_graph,
)
from .gap_analyzer import Gap, find_gaps, gap_tasks
from .opportunity_finder import (
    OPPORTUNITIES_FILENAME,
    OpportunityCandidate,
    find_opportunities,
    load_opportunities,
    opportunities_path,
    opportunity_research_brief,
    save_opportunities,
)
from .project_memory import (
    CATEGORY_FILES,
    digest,
    has_content,
    init_memory,
    load_rules,
    memory_dir,
    record,
    record_rule,
    recent_entries,
)
from .project_model import (
    MODEL_FILENAME,
    ProjectModel,
    ProjectModelBuilder,
    load_model,
    model_path,
    save_model,
)

__all__ = [
    "CATEGORY_FILES",
    "CodeGraph",
    "Gap",
    "GRAPH_FILENAME",
    "MODEL_FILENAME",
    "OPPORTUNITIES_FILENAME",
    "OpportunityCandidate",
    "ProjectModel",
    "ProjectModelBuilder",
    "Symbol",
    "build_code_graph",
    "digest",
    "find_gaps",
    "find_opportunities",
    "gap_tasks",
    "graph_path",
    "has_content",
    "init_memory",
    "load_graph",
    "load_model",
    "load_opportunities",
    "load_rules",
    "memory_dir",
    "model_path",
    "opportunities_path",
    "opportunity_research_brief",
    "record",
    "record_rule",
    "recent_entries",
    "related_files",
    "save_graph",
    "save_model",
    "save_opportunities",
]
