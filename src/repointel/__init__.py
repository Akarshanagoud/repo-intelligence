"""repointel - AI-assisted codebase analysis: search, architecture and insights."""
from .analyzer import Analysis, AnalysisConfig, analyze
from .architecture import (
    Cycle,
    DependencyGraph,
    ModuleNode,
    build_graph,
    graph_to_mermaid,
    render_graph,
)
from .insights import (
    ChurnStats,
    FileInsight,
    RepoInsights,
    analyse,
    find_duplicates,
    git_churn,
    render_insights,
)
from .parsing import (
    ImportEdge,
    ParsedFile,
    Symbol,
    cyclomatic_complexity,
    parse_all,
    parse_file,
)
from .scanner import ScanResult, SourceFile, scan
from .search import (
    CodeChunk,
    CodeEmbedder,
    CodeIndex,
    SearchHit,
    build_index,
    chunk_file,
    split_identifier,
    tokenize_code,
)
from .summarize import RepoSummary, summarise_module, summarise_repository

__version__ = "0.1.0"

__all__ = [
    "Analysis",
    "AnalysisConfig",
    "ChurnStats",
    "CodeChunk",
    "CodeEmbedder",
    "CodeIndex",
    "Cycle",
    "DependencyGraph",
    "FileInsight",
    "ImportEdge",
    "ModuleNode",
    "ParsedFile",
    "RepoInsights",
    "RepoSummary",
    "ScanResult",
    "SearchHit",
    "SourceFile",
    "Symbol",
    "analyse",
    "analyze",
    "build_graph",
    "build_index",
    "chunk_file",
    "cyclomatic_complexity",
    "find_duplicates",
    "git_churn",
    "graph_to_mermaid",
    "parse_all",
    "parse_file",
    "render_graph",
    "render_insights",
    "scan",
    "split_identifier",
    "summarise_module",
    "summarise_repository",
    "tokenize_code",
    "__version__",
]
