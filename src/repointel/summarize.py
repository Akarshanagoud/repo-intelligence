"""Repository and module summarisation.

The default summariser is **extractive and template-driven**: it states what
was measured and quotes docstrings that already exist. It therefore cannot
invent a module's purpose, which is the failure mode that makes generated
codebase documentation untrustworthy — a confident paragraph about what a
module does, written by something that only saw its imports.

A local model can write the prose instead, and when it does the output is
labelled `source: model` and the underlying facts are still attached. The
distinction matters to a reader deciding how much to trust the paragraph.

Every claim in a template summary traces to something countable: a symbol
count, a fan-in number, a docstring that exists in the source.
"""
from __future__ import annotations

import json
import re
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .architecture import DependencyGraph
from .insights import RepoInsights
from .parsing import ParsedFile
from .scanner import ScanResult

#: Directory names that signal a role, used to describe the layout in words.
ROLE_HINTS = {
    "api": "HTTP or RPC interface",
    "routes": "HTTP routing",
    "handlers": "request handlers",
    "models": "data models",
    "schemas": "data schemas",
    "services": "business logic",
    "core": "core domain logic",
    "domain": "core domain logic",
    "db": "database access",
    "database": "database access",
    "repository": "data access",
    "repositories": "data access",
    "migrations": "database migrations",
    "utils": "shared helpers",
    "helpers": "shared helpers",
    "lib": "shared library code",
    "cli": "command line interface",
    "cmd": "command line entry points",
    "config": "configuration",
    "middleware": "request middleware",
    "auth": "authentication and authorisation",
    "tests": "test suite",
    "scripts": "operational scripts",
    "workers": "background processing",
    "tasks": "background tasks",
    "jobs": "scheduled jobs",
    "ui": "user interface",
    "components": "UI components",
}


@dataclass
class ModuleSummary:
    path: str
    purpose: str
    symbols: int
    public_symbols: int
    key_symbols: list[str] = field(default_factory=list)
    fan_in: int = 0
    fan_out: int = 0
    source: str = "template"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "purpose": self.purpose,
            "symbols": self.symbols,
            "public_symbols": self.public_symbols,
            "key_symbols": self.key_symbols,
            "fan_in": self.fan_in,
            "fan_out": self.fan_out,
            "source": self.source,
        }


@dataclass
class RepoSummary:
    name: str
    overview: str
    languages: dict[str, int]
    entry_points: list[str] = field(default_factory=list)
    layout: list[str] = field(default_factory=list)
    key_modules: list[ModuleSummary] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    source: str = "template"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "overview": self.overview,
            "languages": self.languages,
            "entry_points": self.entry_points,
            "layout": self.layout,
            "key_modules": [m.to_dict() for m in self.key_modules],
            "observations": self.observations,
            "source": self.source,
        }

    def render(self) -> str:
        lines = [f"# {self.name}", "", self.overview, ""]

        if self.languages:
            rendered = ", ".join(
                f"{name} ({count} files)" for name, count in self.languages.items()
            )
            lines += [f"**Languages**: {rendered}", ""]

        if self.entry_points:
            lines += ["**Entry points**"]
            lines += [f"- `{path}`" for path in self.entry_points]
            lines.append("")

        if self.layout:
            lines += ["**Layout**"]
            lines += [f"- {item}" for item in self.layout]
            lines.append("")

        if self.key_modules:
            lines += ["**Key modules**", ""]
            for module in self.key_modules:
                lines.append(f"### `{module.path}`")
                lines.append(module.purpose)
                if module.key_symbols:
                    lines.append(
                        f"Defines {', '.join(f'`{s}`' for s in module.key_symbols[:5])}."
                    )
                lines.append(
                    f"Imported by {module.fan_in} module(s); imports {module.fan_out}."
                )
                lines.append("")

        if self.observations:
            lines += ["**Observations**"]
            lines += [f"- {item}" for item in self.observations]
            lines.append("")

        if self.source == "template":
            lines.append(
                "_Generated from static analysis. Every claim above corresponds "
                "to a counted symbol, a resolved import, or a docstring quoted "
                "from the source._"
            )
        else:
            lines.append(
                "_Prose written by a local model from the measured facts above._"
            )
        return "\n".join(lines)


def _first_sentence(text: str, limit: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""
    match = re.search(r"^(.+?[.!?])(\s|$)", cleaned)
    sentence = match.group(1) if match else cleaned
    return sentence[:limit]


def summarise_module(
    parsed: ParsedFile, graph: DependencyGraph | None = None
) -> ModuleSummary:
    """Describe one module from its docstring and its measured shape."""
    node = graph.nodes.get(parsed.file) if graph else None
    public = [s for s in parsed.symbols if s.is_public]

    if parsed.module_docstring.strip():
        # Prefer what the author already said.
        purpose = _first_sentence(parsed.module_docstring)
    else:
        kinds = Counter(s.kind for s in parsed.symbols)
        if not kinds:
            purpose = "No symbols detected; likely configuration or data."
        else:
            described = ", ".join(
                f"{count} {kind}{'es' if kind == 'class' and count != 1 else 's' if count != 1 else ''}"
                for kind, count in kinds.most_common(3)
            )
            purpose = f"Defines {described}. No module docstring."

    key = sorted(public, key=lambda s: -s.complexity)[:5]
    return ModuleSummary(
        path=parsed.file,
        purpose=purpose,
        symbols=len(parsed.symbols),
        public_symbols=len(public),
        key_symbols=[s.name for s in key],
        fan_in=node.fan_in if node else 0,
        fan_out=node.fan_out if node else 0,
    )


def find_entry_points(scan_result: ScanResult, parsed: list[ParsedFile]) -> list[str]:
    """Files that look like where execution starts."""
    entries: list[str] = []
    names = {"main.py", "__main__.py", "app.py", "cli.py", "manage.py",
             "server.py", "index.js", "index.ts", "main.go", "main.rs"}

    for source in scan_result.files:
        base = source.relative.rsplit("/", 1)[-1]
        if base in names or '__name__ == "__main__"' in source.text or "__name__ == '__main__'" in source.text:
            entries.append(source.relative)

    # Console scripts declared in packaging are the most reliable signal - but
    # only inside the [project.scripts] table. Scanning the whole file matches
    # `Homepage = "https://..."` and reports `https.py` as an entry point.
    for source in scan_result.files:
        if not source.relative.endswith("pyproject.toml"):
            continue
        section = re.search(
            r"^\[project\.scripts\]\s*$(.*?)(?=^\[|\Z)", source.text, re.M | re.S
        )
        if section is None:
            continue
        for match in re.finditer(r"^\s*[\w-]+\s*=\s*\"([\w.]+):", section.group(1), re.M):
            entries.append(f"{match.group(1).replace('.', '/')}.py (console script)")

    return sorted(dict.fromkeys(entries))[:10]


def describe_layout(scan_result: ScanResult) -> list[str]:
    """Turn the directory structure into sentences."""
    packages: Counter[str] = Counter()
    for source in scan_result.files:
        parts = source.relative.split("/")
        if len(parts) > 1:
            packages[parts[0] if parts[0] not in ("src", "lib") else "/".join(parts[:2])] += 1

    described: list[str] = []
    for package, count in packages.most_common(10):
        leaf = package.rsplit("/", 1)[-1]
        role = ROLE_HINTS.get(leaf.lower())
        suffix = f" - {role}" if role else ""
        described.append(f"`{package}/` ({count} files){suffix}")
    return described


def build_observations(
    insights: RepoInsights, graph: DependencyGraph, scan_result: ScanResult
) -> list[str]:
    """The findings worth putting in front of a reader, stated as facts."""
    observations: list[str] = []
    totals = insights.totals()

    cycles = graph.find_cycles()
    if cycles:
        largest = cycles[0]
        observations.append(
            f"{len(cycles)} import cycle(s) detected; the largest involves "
            f"{largest.size} modules ({largest.render()}). Modules in a cycle "
            f"cannot be tested or extracted independently."
        )
    else:
        observations.append("No import cycles: the module graph is acyclic.")

    hubs = graph.hubs(1)
    if hubs and hubs[0].fan_in > 2:
        share = hubs[0].fan_in / max(len(graph.nodes) - 1, 1)
        observations.append(
            f"`{hubs[0].path}` is imported by {hubs[0].fan_in} modules "
            f"({share:.0%} of the codebase), so changes to it are expensive."
        )

    coverage = totals["doc_coverage"]
    observations.append(
        f"{coverage:.0%} of public symbols have a docstring "
        f"({totals['documented_public_symbols']} of {totals['public_symbols']})."
    )

    if totals["test_files"] == 0:
        observations.append("No test files were found.")
    else:
        observations.append(
            f"{totals['test_files']} test file(s) for {totals['source_files']} "
            f"source file(s), a ratio of {totals['test_ratio']:.2f}."
        )

    riskiest = insights.riskiest(1)
    if riskiest:
        file = riskiest[0]
        if insights.churn_available:
            observations.append(
                f"Highest-risk file is `{file.path}` (complexity "
                f"{file.total_complexity}, {file.commits} recent commits)."
            )
        else:
            observations.append(
                f"Most complex file is `{file.path}` (complexity "
                f"{file.total_complexity}); git history was unavailable, so "
                f"churn is unknown."
            )

    complex_symbols = insights.most_complex_symbols(1)
    if complex_symbols and complex_symbols[0].complexity > 15:
        symbol = complex_symbols[0]
        observations.append(
            f"`{symbol.name}` in `{symbol.file}` has cyclomatic complexity "
            f"{symbol.complexity}, well above the conventional threshold of 10."
        )

    if graph.external:
        top = sorted(graph.external.items(), key=lambda kv: -kv[1])[:3]
        observations.append(
            "Main third-party dependencies: "
            + ", ".join(f"`{name}`" for name, _ in top)
            + "."
        )
    else:
        observations.append("No third-party imports: standard library only.")

    parse_failures = [p for p in scan_result.files if False]
    del parse_failures
    return observations


def summarise_repository(
    scan_result: ScanResult,
    parsed: list[ParsedFile],
    graph: DependencyGraph,
    insights: RepoInsights,
    name: str | None = None,
    max_modules: int = 6,
) -> RepoSummary:
    """Build the repository summary from measured facts only."""
    totals = insights.totals()
    repo_name = name or scan_result.root.name

    overview = (
        f"{repo_name} is a {_dominant_language(scan_result)} codebase of "
        f"{totals['lines']:,} lines across {totals['files']} files, defining "
        f"{totals['symbols']} symbols ({totals['public_symbols']} public). "
        f"The module graph has {len(graph.edges)} internal dependencies "
        f"between {len(graph.nodes)} modules."
    )

    by_path = {p.file: p for p in parsed}
    # Rank modules for the summary by how central they are, not how large:
    # the module everything imports explains the codebase better than the
    # longest one.
    central = sorted(
        (n for n in graph.nodes.values() if not n.is_test),
        key=lambda n: (-(n.fan_in * 2 + n.fan_out), n.path),
    )[:max_modules]

    key_modules = [
        summarise_module(by_path[node.path], graph)
        for node in central
        if node.path in by_path
    ]

    return RepoSummary(
        name=repo_name,
        overview=overview,
        languages=scan_result.by_language(),
        entry_points=find_entry_points(scan_result, parsed),
        layout=describe_layout(scan_result),
        key_modules=key_modules,
        observations=build_observations(insights, graph, scan_result),
    )


def _dominant_language(scan_result: ScanResult) -> str:
    by_lines = scan_result.lines_by_language()
    code_only = {
        k: v for k, v in by_lines.items()
        if k not in {"markdown", "json", "yaml", "toml"}
    }
    return next(iter(code_only), "mixed-language") if code_only else "mixed-language"


def summarise_with_model(
    summary: RepoSummary,
    base_url: str = "http://localhost:11434",
    model: str = "llama3.2",
    timeout: float = 120.0,
) -> RepoSummary:
    """Rewrite the overview as prose using a local model.

    The model receives only the *measured facts*, never the source, so there is
    nothing for it to invent about behaviour it has not seen. If it fails or
    returns nothing, the template summary is kept - a summariser that goes
    silent when the model is down is worse than one that stays factual.
    """
    facts = json.dumps(
        {
            "name": summary.name,
            "languages": summary.languages,
            "entry_points": summary.entry_points,
            "layout": summary.layout,
            "modules": [m.to_dict() for m in summary.key_modules],
            "observations": summary.observations,
        },
        indent=2,
    )
    prompt = (
        "Write a concise 4-6 sentence overview of this codebase for a developer "
        "joining the project. Use ONLY the measured facts below. Do not "
        "speculate about behaviour that is not stated.\n\n" + facts
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except Exception:  # noqa: BLE001 - a model outage must not lose the summary
        return summary

    text = (data.get("response") or "").strip()
    if not text:
        return summary

    summary.overview = text
    summary.source = "model"
    return summary
