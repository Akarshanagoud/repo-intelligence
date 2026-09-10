"""Architecture discovery: dependency graph, layering, cycles, coupling.

The useful output of this module is not a picture. It is a short list of
specific claims a reader can check:

* these three modules form an import cycle;
* this module is imported by 40% of the codebase, so changing it is expensive;
* this "utils" module has fan-in from every layer, which is what "utils" means
  in practice;
* the inferred layering is violated in exactly these four places.

Cycles get the most attention because they are the defect with the clearest
cost: they make modules impossible to test in isolation, impossible to extract,
and impossible to reason about in either direction. They are also invisible in
review, because each individual import looks fine.
"""
from __future__ import annotations

import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .parsing import ImportEdge, ParsedFile

#: Standard-library module names, resolved at runtime where available.
#:
#: Separated from third-party imports because "external dependencies" that
#: turn out to be `json`, `typing` and `pathlib` tell a reader nothing. What
#: they want to know is what the project actually depends on.
try:  # Python 3.10+
    STDLIB: frozenset[str] = frozenset(sys.stdlib_module_names)  # type: ignore[attr-defined]
except AttributeError:  # pragma: no cover - very old interpreters
    STDLIB = frozenset(
        """
        abc argparse ast asyncio base64 collections contextlib copy csv
        dataclasses datetime enum functools glob hashlib heapq io itertools
        json logging math os pathlib random re shutil socket sqlite3 statistics
        string struct subprocess sys tempfile threading time typing unittest
        urllib uuid warnings
        """.split()  # noqa: SIM905 - readable as prose
    )

#: Standard-library names for the other languages the parser handles.
#:
#: Without these, Go's `errors` and Node's `fs` are reported as third-party
#: dependencies, which is exactly the noise the split was introduced to remove.
#: The trade is a small false-negative risk (a third-party package genuinely
#: named `errors` would be hidden), which is the right way round: a missing
#: entry in a dependency list is less misleading than a fictional one.
GO_STDLIB = frozenset(
    """
    errors fmt io log net os strings strconv sync time context bytes bufio sort
    math encoding regexp path reflect testing flag database crypto
    """.split()  # noqa: SIM905 - readable as prose
)

NODE_BUILTINS = frozenset(
    """
    fs path http https crypto os util events stream url querystring buffer
    child_process cluster net dns zlib assert readline worker_threads
    """.split()  # noqa: SIM905 - readable as prose
)

#: Union used when classifying an import, since the graph is language-agnostic.
ALL_STDLIB = STDLIB | GO_STDLIB | NODE_BUILTINS


@dataclass
class ModuleNode:
    path: str
    package: str
    lines: int = 0
    symbols: int = 0
    is_test: bool = False
    imports: set[str] = field(default_factory=set)
    imported_by: set[str] = field(default_factory=set)

    @property
    def fan_out(self) -> int:
        """Modules this one depends on. High fan-out means hard to understand."""
        return len(self.imports)

    @property
    def fan_in(self) -> int:
        """Modules that depend on this one. High fan-in means hard to change."""
        return len(self.imported_by)

    @property
    def instability(self) -> float:
        """Martin's instability metric: fan_out / (fan_in + fan_out).

        0 is maximally stable (everything depends on it, it depends on nothing)
        and 1 is maximally unstable. Neither extreme is wrong; a *stable module
        that changes often* is the problem.
        """
        total = self.fan_in + self.fan_out
        return self.fan_out / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "package": self.package,
            "lines": self.lines,
            "symbols": self.symbols,
            "fan_in": self.fan_in,
            "fan_out": self.fan_out,
            "instability": round(self.instability, 3),
            "is_test": self.is_test,
        }


@dataclass
class Cycle:
    modules: list[str]

    @property
    def size(self) -> int:
        return len(self.modules)

    def to_dict(self) -> dict[str, Any]:
        return {"size": self.size, "modules": self.modules}

    def render(self) -> str:
        return " -> ".join(self.modules + [self.modules[0]])


@dataclass
class DependencyGraph:
    nodes: dict[str, ModuleNode] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    #: Third-party imports only.
    external: dict[str, int] = field(default_factory=dict)
    #: Standard-library imports, tracked separately.
    stdlib: dict[str, int] = field(default_factory=dict)
    unresolved: int = 0

    def add_node(self, node: ModuleNode) -> None:
        self.nodes[node.path] = node

    def add_edge(self, source: str, target: str) -> None:
        if source == target or target not in self.nodes or source not in self.nodes:
            return
        if target in self.nodes[source].imports:
            return
        self.nodes[source].imports.add(target)
        self.nodes[target].imported_by.add(source)
        self.edges.append((source, target))

    # -- cycles ---------------------------------------------------------------
    def find_cycles(self, max_cycles: int = 20) -> list[Cycle]:
        """Strongly connected components with more than one member.

        Tarjan's algorithm, iterative rather than recursive: a deep import
        graph would blow the Python stack, and crashing on a large repository
        is not an acceptable failure mode for an analysis tool.
        """
        index_counter = 0
        indices: dict[str, int] = {}
        low_link: dict[str, int] = {}
        on_stack: dict[str, bool] = {}
        stack: list[str] = []
        components: list[list[str]] = []

        for root in sorted(self.nodes):
            if root in indices:
                continue

            work: list[tuple[str, int]] = [(root, 0)]
            while work:
                node, child_index = work[-1]

                if child_index == 0:
                    indices[node] = low_link[node] = index_counter
                    index_counter += 1
                    stack.append(node)
                    on_stack[node] = True

                children = sorted(self.nodes[node].imports)
                recursed = False
                while child_index < len(children):
                    child = children[child_index]
                    child_index += 1
                    if child not in indices:
                        work[-1] = (node, child_index)
                        work.append((child, 0))
                        recursed = True
                        break
                    if on_stack.get(child):
                        low_link[node] = min(low_link[node], indices[child])
                if recursed:
                    continue

                work[-1] = (node, child_index)
                work.pop()
                if work:
                    parent = work[-1][0]
                    low_link[parent] = min(low_link[parent], low_link[node])

                if low_link[node] == indices[node]:
                    component: list[str] = []
                    while True:
                        member = stack.pop()
                        on_stack[member] = False
                        component.append(member)
                        if member == node:
                            break
                    if len(component) > 1:
                        components.append(sorted(component))

        components.sort(key=len, reverse=True)
        return [Cycle(modules=c) for c in components[:max_cycles]]

    # -- layering -------------------------------------------------------------
    def infer_layers(self) -> dict[str, int]:
        """Assign each module a depth by longest path from a leaf.

        Layer 0 modules import nothing internal; higher layers build on lower
        ones. Computed by longest path rather than shortest, so a module that
        sits on top of a deep chain is reported at its true depth.
        """
        layers: dict[str, int] = {}
        in_degree = {path: len(node.imports) for path, node in self.nodes.items()}
        queue = deque(sorted(p for p, d in in_degree.items() if d == 0))
        for path in queue:
            layers[path] = 0

        while queue:
            current = queue.popleft()
            for dependent in sorted(self.nodes[current].imported_by):
                in_degree[dependent] -= 1
                layers[dependent] = max(layers.get(dependent, 0), layers[current] + 1)
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        # Modules inside a cycle never reach in-degree zero.
        for path in self.nodes:
            layers.setdefault(path, -1)
        return layers

    def layer_violations(self) -> list[tuple[str, str]]:
        """Imports that point from a lower layer up to a higher one.

        In an acyclic graph this is empty by construction, so a non-empty
        result is a direct consequence of the cycles reported above - which is
        exactly the point: cycles are not an aesthetic complaint.
        """
        layers = self.infer_layers()
        violations: list[tuple[str, str]] = []
        for source, target in self.edges:
            source_layer, target_layer = layers.get(source, -1), layers.get(target, -1)
            if source_layer >= 0 and target_layer >= 0 and target_layer > source_layer:
                violations.append((source, target))
        return violations

    # -- summaries ------------------------------------------------------------
    def hubs(self, limit: int = 10) -> list[ModuleNode]:
        """Modules everything depends on. Changing these is expensive."""
        return sorted(
            (n for n in self.nodes.values() if not n.is_test),
            key=lambda n: -n.fan_in,
        )[:limit]

    def complex_dependents(self, limit: int = 10) -> list[ModuleNode]:
        """Modules that depend on the most others. Hard to understand or move."""
        return sorted(
            (n for n in self.nodes.values() if not n.is_test),
            key=lambda n: -n.fan_out,
        )[:limit]

    def packages(self) -> dict[str, dict[str, Any]]:
        """Roll modules up into packages, with coupling between them."""
        rollup: dict[str, dict[str, Any]] = {}
        for node in self.nodes.values():
            entry = rollup.setdefault(
                node.package,
                {"modules": 0, "lines": 0, "symbols": 0, "depends_on": set()},
            )
            entry["modules"] += 1
            entry["lines"] += node.lines
            entry["symbols"] += node.symbols
            for target in node.imports:
                target_package = self.nodes[target].package
                if target_package != node.package:
                    entry["depends_on"].add(target_package)

        return {
            name: {**data, "depends_on": sorted(data["depends_on"])}
            for name, data in sorted(rollup.items())
        }

    def to_dict(self) -> dict[str, Any]:
        cycles = self.find_cycles()
        return {
            "modules": len(self.nodes),
            "internal_edges": len(self.edges),
            "third_party_dependencies": dict(
                sorted(self.external.items(), key=lambda kv: -kv[1])[:20]
            ),
            "stdlib_dependencies": dict(
                sorted(self.stdlib.items(), key=lambda kv: -kv[1])[:20]
            ),
            "cycles": [c.to_dict() for c in cycles],
            "layer_violations": len(self.layer_violations()),
            "hubs": [n.to_dict() for n in self.hubs(5)],
            "packages": {
                name: {k: v for k, v in data.items() if k != "depends_on"}
                | {"depends_on": data["depends_on"]}
                for name, data in self.packages().items()
            },
        }


def _module_key(path: str) -> str:
    return path.removesuffix(".py").removesuffix(".pyi").replace("\\", "/")


def _package_of(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    return "/".join(parts[:-1]) or "."


def resolve_import(
    edge: ImportEdge, module_index: dict[str, str], source_file: str
) -> str | None:
    """Map an import target onto a file in this repository, if it is one.

    Handles absolute dotted modules, package `__init__` imports, relative
    paths, and JS/TS relative specifiers with implicit extensions.
    """
    target = edge.target.replace("\\", "/")

    # Already resolved to a path by the parser (Python relative imports).
    if target in module_index:
        return module_index[target]

    # Dotted module path: promptguard.policy.pii -> .../policy/pii.py
    dotted = target.replace(".", "/")
    for candidate in (dotted, f"{dotted}/__init__"):
        if candidate in module_index:
            return module_index[candidate]
        # Try progressively shorter suffixes: an import of `src.pkg.mod` should
        # match `pkg/mod` when the repo root is above `src`.
        parts = dotted.split("/")
        for start in range(len(parts)):
            suffix = "/".join(parts[start:])
            if suffix and suffix in module_index:
                return module_index[suffix]

    # Relative specifier from JS/TS: ./foo, ../bar/baz
    if target.startswith("."):
        base = source_file.rsplit("/", 1)[0] if "/" in source_file else ""
        pieces = [p for p in f"{base}/{target}".split("/") if p not in ("", ".")]
        resolved: list[str] = []
        for piece in pieces:
            if piece == "..":
                if resolved:
                    resolved.pop()
            else:
                resolved.append(piece)
        candidate = "/".join(resolved)
        for suffix in ("", "/index"):
            if candidate + suffix in module_index:
                return module_index[candidate + suffix]

    return None


def build_graph(parsed: list[ParsedFile], file_meta: dict[str, dict[str, Any]] | None = None) -> DependencyGraph:
    """Build the internal dependency graph from parsed files."""
    graph = DependencyGraph()
    meta = file_meta or {}

    module_index: dict[str, str] = {}
    for parsed_file in parsed:
        key = _module_key(parsed_file.file)
        module_index[key] = parsed_file.file
        # Also index without a leading src/ or lib/ directory, since imports
        # are written relative to the package root, not the repository root.
        for prefix in ("src/", "lib/", "app/"):
            if key.startswith(prefix):
                module_index.setdefault(key[len(prefix) :], parsed_file.file)

    for parsed_file in parsed:
        info = meta.get(parsed_file.file, {})
        graph.add_node(
            ModuleNode(
                path=parsed_file.file,
                package=_package_of(parsed_file.file),
                lines=int(info.get("lines", 0)),
                symbols=len(parsed_file.symbols),
                is_test=bool(info.get("is_test", False)),
            )
        )

    external: dict[str, int] = defaultdict(int)
    stdlib: dict[str, int] = defaultdict(int)
    for parsed_file in parsed:
        for edge in parsed_file.imports:
            resolved = resolve_import(edge, module_index, parsed_file.file)
            if resolved is not None:
                edge.internal = True
                edge.resolved_file = resolved
                graph.add_edge(parsed_file.file, resolved)
            else:
                # Record the top-level package name, not the full path, so
                # `numpy.linalg` and `numpy` count as the same dependency.
                root = edge.target.split(".")[0].split("/")[0]
                if not root or root.startswith("."):
                    continue
                if root in ALL_STDLIB:
                    stdlib[root] += 1
                else:
                    external[root] += 1

    graph.external = dict(external)
    graph.stdlib = dict(stdlib)
    return graph


def render_graph(graph: DependencyGraph, limit: int = 15) -> str:
    """Human-readable architecture summary."""
    lines: list[str] = []
    cycles = graph.find_cycles()

    lines.append(f"modules            : {len(graph.nodes)}")
    lines.append(f"internal edges     : {len(graph.edges)}")
    lines.append(f"import cycles      : {len(cycles)}")
    lines.append(f"layer violations   : {len(graph.layer_violations())}")

    if cycles:
        lines.append("\nimport cycles (each makes its modules untestable in isolation):")
        for cycle in cycles[:5]:
            lines.append(f"  [{cycle.size}] {cycle.render()}")

    hubs = graph.hubs(limit=8)
    if hubs and hubs[0].fan_in:
        lines.append("\nmost depended-upon modules (expensive to change):")
        for node in hubs:
            if not node.fan_in:
                continue
            share = node.fan_in / max(len(graph.nodes) - 1, 1)
            lines.append(
                f"  {node.path:<48} fan-in {node.fan_in:>3} ({share:.0%} of modules)"
            )

    complex_modules = graph.complex_dependents(limit=5)
    if complex_modules and complex_modules[0].fan_out:
        lines.append("\nmost dependent modules (hard to understand in isolation):")
        for node in complex_modules:
            if not node.fan_out:
                continue
            lines.append(
                f"  {node.path:<48} fan-out {node.fan_out:>3}  "
                f"instability {node.instability:.2f}"
            )

    if graph.external:
        top = sorted(graph.external.items(), key=lambda kv: -kv[1])[:limit]
        lines.append("\nthird-party dependencies by import count:")
        for name, count in top:
            lines.append(f"  {name:<32} {count}")
    else:
        lines.append("\nthird-party dependencies: none (standard library only)")

    if graph.stdlib:
        top = sorted(graph.stdlib.items(), key=lambda kv: -kv[1])[:8]
        rendered = ", ".join(f"{name} ({count})" for name, count in top)
        lines.append(f"\nmost-used standard library modules: {rendered}")

    return "\n".join(lines)


def graph_to_mermaid(graph: DependencyGraph, max_nodes: int = 40) -> str:
    """Render the package-level graph as a Mermaid diagram.

    Package level rather than module level on purpose: a module-level diagram
    of a real repository is an unreadable hairball, and a diagram nobody can
    read is worse than a table.
    """
    packages = graph.packages()
    if not packages:
        return "graph TD\n  empty[no modules found]"

    ranked = sorted(packages.items(), key=lambda kv: -kv[1]["lines"])[:max_nodes]
    included = {name for name, _ in ranked}

    def node_id(name: str) -> str:
        return "p_" + "".join(c if c.isalnum() else "_" for c in name)

    lines = ["graph TD"]
    for name, data in ranked:
        label = f"{name}<br/>{data['modules']} modules, {data['lines']} lines"
        lines.append(f'  {node_id(name)}["{label}"]')

    seen: set[tuple[str, str]] = set()
    for name, data in ranked:
        for dependency in data["depends_on"]:
            if dependency not in included or (name, dependency) in seen:
                continue
            seen.add((name, dependency))
            lines.append(f"  {node_id(name)} --> {node_id(dependency)}")

    return "\n".join(lines)
