"""Symbol extraction.

Python is parsed with the standard library `ast`, so the results are exact:
real qualified names, real decorators, real nesting, real complexity. Other
languages use structural patterns, which is a genuine downgrade and is labelled
as such on every symbol (`parser="ast"` vs `parser="pattern"`) rather than
presented as equivalent.

That labelling matters more than it sounds. A tool that silently mixes exact
and approximate extraction produces an architecture diagram where some edges
are facts and others are guesses, with no way to tell which — and the guesses
are exactly where a reader's confidence should drop.

Cyclomatic complexity is computed properly for Python (counting the branch
points that actually create paths) and approximated by keyword frequency
elsewhere.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

from .scanner import SourceFile


@dataclass
class Symbol:
    """A named definition in a source file."""

    name: str
    kind: str  # function | method | class | interface | struct | constant
    file: str
    line: int
    end_line: int
    language: str
    #: Exact for Python (``module.Class.method``), best-effort elsewhere.
    qualified_name: str = ""
    signature: str = ""
    docstring: str = ""
    decorators: list[str] = field(default_factory=list)
    complexity: int = 1
    #: "ast" when exactly parsed, "pattern" when matched structurally.
    parser: str = "pattern"
    parent: str | None = None
    is_public: bool = True
    is_async: bool = False

    @property
    def line_count(self) -> int:
        return max(self.end_line - self.line + 1, 1)

    @property
    def has_docstring(self) -> bool:
        return bool(self.docstring.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name or self.name,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "lines": self.line_count,
            "language": self.language,
            "signature": self.signature,
            "complexity": self.complexity,
            "has_docstring": self.has_docstring,
            "decorators": self.decorators,
            "parser": self.parser,
            "is_public": self.is_public,
        }


@dataclass
class ImportEdge:
    """A dependency from one file to a module."""

    source_file: str
    target: str
    line: int
    #: True when the import resolves inside the repository.
    internal: bool = False
    resolved_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.source_file,
            "to": self.resolved_file or self.target,
            "internal": self.internal,
            "line": self.line,
        }


@dataclass
class ParsedFile:
    file: str
    language: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportEdge] = field(default_factory=list)
    module_docstring: str = ""
    parse_error: str | None = None
    parser: str = "pattern"

    @property
    def public_symbols(self) -> list[Symbol]:
        return [s for s in self.symbols if s.is_public]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "language": self.language,
            "parser": self.parser,
            "symbols": len(self.symbols),
            "imports": len(self.imports),
            "has_module_docstring": bool(self.module_docstring.strip()),
            "parse_error": self.parse_error,
        }


# --- Python, via ast ---------------------------------------------------------
#: Node types that create an additional execution path.
_BRANCH_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
    ast.With, ast.AsyncWith, ast.Assert, ast.IfExp,
)


def cyclomatic_complexity(node: ast.AST) -> int:
    """McCabe complexity: branch points plus one.

    Boolean operators count too - ``if a and b`` has two paths, not one - and
    comprehension conditions count, because a comprehension with a filter is a
    loop with a branch however compact it looks.
    """
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, _BRANCH_NODES):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += len(child.values) - 1
        elif isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            complexity += sum(len(generator.ifs) for generator in child.generators)
        elif isinstance(child, ast.Match):
            complexity += len(child.cases)
    return complexity


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    parts: list[str] = []
    arguments = node.args

    positional = arguments.posonlyargs + arguments.args
    defaults_offset = len(positional) - len(arguments.defaults)
    for index, argument in enumerate(positional):
        rendered = argument.arg
        if argument.annotation is not None:
            rendered += f": {ast.unparse(argument.annotation)}"
        if index >= defaults_offset:
            rendered += f" = {ast.unparse(arguments.defaults[index - defaults_offset])}"
        parts.append(rendered)

    if arguments.vararg is not None:
        parts.append(f"*{arguments.vararg.arg}")
    elif arguments.kwonlyargs:
        parts.append("*")

    for argument, default in zip(
        arguments.kwonlyargs, arguments.kw_defaults, strict=True
    ):
        rendered = argument.arg
        if argument.annotation is not None:
            rendered += f": {ast.unparse(argument.annotation)}"
        if default is not None:
            rendered += f" = {ast.unparse(default)}"
        parts.append(rendered)

    if arguments.kwarg is not None:
        parts.append(f"**{arguments.kwarg.arg}")

    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(parts)}){returns}"


def parse_python(source: SourceFile) -> ParsedFile:
    parsed = ParsedFile(file=source.relative, language="python", parser="ast")

    try:
        tree = ast.parse(source.text, filename=source.relative)
    except SyntaxError as exc:
        # A file that does not parse is reported, not silently dropped: a
        # missing module in the architecture graph is worse than a flagged one.
        parsed.parse_error = f"line {exc.lineno}: {exc.msg}"
        return parsed

    parsed.module_docstring = ast.get_docstring(tree) or ""
    module_name = source.relative.replace("/", ".").removesuffix(".py")

    def walk(node: ast.AST, parent_name: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{parent_name}.{child.name}" if parent_name else child.name
                parsed.symbols.append(
                    Symbol(
                        name=child.name,
                        kind="method" if parent_name else "function",
                        file=source.relative,
                        line=child.lineno,
                        end_line=getattr(child, "end_lineno", child.lineno) or child.lineno,
                        language="python",
                        qualified_name=f"{module_name}.{qualified}",
                        signature=_signature(child),
                        docstring=ast.get_docstring(child) or "",
                        decorators=[ast.unparse(d) for d in child.decorator_list],
                        complexity=cyclomatic_complexity(child),
                        parser="ast",
                        parent=parent_name,
                        is_public=not child.name.startswith("_"),
                        is_async=isinstance(child, ast.AsyncFunctionDef),
                    )
                )
                walk(child, qualified)

            elif isinstance(child, ast.ClassDef):
                qualified = f"{parent_name}.{child.name}" if parent_name else child.name
                bases = [ast.unparse(b) for b in child.bases]
                parsed.symbols.append(
                    Symbol(
                        name=child.name,
                        kind="class",
                        file=source.relative,
                        line=child.lineno,
                        end_line=getattr(child, "end_lineno", child.lineno) or child.lineno,
                        language="python",
                        qualified_name=f"{module_name}.{qualified}",
                        signature=f"class {child.name}({', '.join(bases)})"
                        if bases
                        else f"class {child.name}",
                        docstring=ast.get_docstring(child) or "",
                        decorators=[ast.unparse(d) for d in child.decorator_list],
                        complexity=cyclomatic_complexity(child),
                        parser="ast",
                        parent=parent_name,
                        is_public=not child.name.startswith("_"),
                    )
                )
                walk(child, qualified)

            elif isinstance(child, ast.Import):
                for alias in child.names:
                    parsed.imports.append(
                        ImportEdge(source.relative, alias.name, child.lineno)
                    )

            elif isinstance(child, ast.ImportFrom):
                # A relative import is by definition internal; resolve it
                # against the importing file's package.
                if child.level:
                    package = source.relative.rsplit("/", child.level)[0]
                    target = f"{package}/{child.module.replace('.', '/')}" if child.module else package
                    parsed.imports.append(
                        ImportEdge(source.relative, target, child.lineno, internal=True)
                    )
                elif child.module:
                    parsed.imports.append(
                        ImportEdge(source.relative, child.module, child.lineno)
                    )

            elif isinstance(child, ast.Assign) and parent_name is None:
                # Module-level constants: UPPER_CASE names only.
                for target in child.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        parsed.symbols.append(
                            Symbol(
                                name=target.id,
                                kind="constant",
                                file=source.relative,
                                line=child.lineno,
                                end_line=getattr(child, "end_lineno", child.lineno)
                                or child.lineno,
                                language="python",
                                qualified_name=f"{module_name}.{target.id}",
                                parser="ast",
                                is_public=not target.id.startswith("_"),
                            )
                        )

    walk(tree, None)
    return parsed


# --- other languages, via structural patterns --------------------------------
@dataclass(frozen=True)
class LanguagePatterns:
    symbols: list[tuple[str, str]]  # (kind, regex with a `name` group)
    imports: list[str]
    #: Keywords that indicate a branch, for approximate complexity.
    branch_keywords: tuple[str, ...] = (
        "if", "else if", "elif", "for", "while", "case", "catch", "&&", "||", "?",
    )


PATTERNS: dict[str, LanguagePatterns] = {
    "javascript": LanguagePatterns(
        symbols=[
            ("class", r"(?m)^\s*(?:export\s+)?(?:default\s+)?class\s+(?P<name>\w+)"),
            ("function", r"(?m)^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>\w+)"),
            ("function", r"(?m)^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"),
            ("method", r"(?m)^\s{2,}(?:async\s+)?(?P<name>\w+)\s*\([^)]*\)\s*\{"),
        ],
        imports=[
            r"""(?m)^\s*import\s+(?:[^'"]*from\s+)?['"](?P<target>[^'"]+)['"]""",
            r"""(?m)require\(\s*['"](?P<target>[^'"]+)['"]\s*\)""",
        ],
    ),
    "typescript": LanguagePatterns(
        symbols=[
            ("interface", r"(?m)^\s*(?:export\s+)?interface\s+(?P<name>\w+)"),
            ("type", r"(?m)^\s*(?:export\s+)?type\s+(?P<name>\w+)\s*="),
            ("class", r"(?m)^\s*(?:export\s+)?(?:abstract\s+)?class\s+(?P<name>\w+)"),
            ("function", r"(?m)^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>\w+)"),
            ("function", r"(?m)^\s*(?:export\s+)?(?:const|let)\s+(?P<name>\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?\("),
        ],
        imports=[
            r"""(?m)^\s*import\s+(?:type\s+)?(?:[^'"]*from\s+)?['"](?P<target>[^'"]+)['"]""",
        ],
    ),
    "go": LanguagePatterns(
        symbols=[
            ("function", r"(?m)^func\s+(?P<name>\w+)\s*\("),
            ("method", r"(?m)^func\s*\([^)]*\)\s*(?P<name>\w+)\s*\("),
            ("struct", r"(?m)^type\s+(?P<name>\w+)\s+struct"),
            ("interface", r"(?m)^type\s+(?P<name>\w+)\s+interface"),
        ],
        imports=[r"""(?m)^\s*(?:\w+\s+)?"(?P<target>[\w./-]+)"\s*$"""],
        branch_keywords=("if", "for", "switch", "case", "select", "&&", "||"),
    ),
    "java": LanguagePatterns(
        symbols=[
            ("class", r"(?m)^\s*(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|final\s+)?class\s+(?P<name>\w+)"),
            ("interface", r"(?m)^\s*(?:public\s+)?interface\s+(?P<name>\w+)"),
            ("method", r"(?m)^\s+(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],\s]+\s+(?P<name>\w+)\s*\("),
        ],
        imports=[r"(?m)^import\s+(?:static\s+)?(?P<target>[\w.]+)"],
    ),
    "rust": LanguagePatterns(
        symbols=[
            ("function", r"(?m)^\s*(?:pub\s+)?(?:async\s+)?fn\s+(?P<name>\w+)"),
            ("struct", r"(?m)^\s*(?:pub\s+)?struct\s+(?P<name>\w+)"),
            ("enum", r"(?m)^\s*(?:pub\s+)?enum\s+(?P<name>\w+)"),
            ("trait", r"(?m)^\s*(?:pub\s+)?trait\s+(?P<name>\w+)"),
        ],
        imports=[r"(?m)^\s*use\s+(?P<target>[\w:]+)"],
        branch_keywords=("if", "match", "for", "while", "&&", "||"),
    ),
    "ruby": LanguagePatterns(
        symbols=[
            ("class", r"(?m)^\s*class\s+(?P<name>\w+)"),
            ("module", r"(?m)^\s*module\s+(?P<name>\w+)"),
            ("method", r"(?m)^\s*def\s+(?P<name>[\w?!=]+)"),
        ],
        imports=[r"""(?m)^\s*require(?:_relative)?\s+['"](?P<target>[^'"]+)['"]"""],
        branch_keywords=("if", "elsif", "unless", "while", "case", "when", "&&", "||"),
    ),
}

_COMMENT_PATTERNS = {
    "javascript": (r"//[^\n]*", r"/\*.*?\*/"),
    "typescript": (r"//[^\n]*", r"/\*.*?\*/"),
    "go": (r"//[^\n]*", r"/\*.*?\*/"),
    "java": (r"//[^\n]*", r"/\*.*?\*/"),
    "rust": (r"//[^\n]*", r"/\*.*?\*/"),
    "ruby": (r"#[^\n]*",),
    "python": (r"#[^\n]*",),
}


def strip_comments(text: str, language: str) -> str:
    """Remove comments before pattern matching.

    Without this, a commented-out function definition becomes a symbol and a
    commented-out import becomes an architecture edge.
    """
    for pattern in _COMMENT_PATTERNS.get(language, ()):
        text = re.sub(pattern, "", text, flags=re.DOTALL)
    return text


def approximate_complexity(body: str, keywords: tuple[str, ...]) -> int:
    """Keyword-frequency complexity for languages without a real parser."""
    complexity = 1
    for keyword in keywords:
        if keyword.isalpha():
            complexity += len(re.findall(rf"\b{re.escape(keyword)}\b", body))
        else:
            complexity += body.count(keyword)
    return complexity


def parse_with_patterns(source: SourceFile) -> ParsedFile:
    parsed = ParsedFile(file=source.relative, language=source.language, parser="pattern")
    rules = PATTERNS.get(source.language)
    if rules is None:
        return parsed

    cleaned = strip_comments(source.text, source.language)
    line_starts = [0]
    for index, character in enumerate(cleaned):
        if character == "\n":
            line_starts.append(index + 1)

    def line_of(offset: int) -> int:
        low, high = 0, len(line_starts)
        while low < high:
            mid = (low + high) // 2
            if line_starts[mid] <= offset:
                low = mid + 1
            else:
                high = mid
        return low

    seen: set[tuple[str, int]] = set()
    for kind, pattern in rules.symbols:
        for match in re.finditer(pattern, cleaned):
            name = match.group("name")
            line = line_of(match.start())
            if (name, line) in seen:
                continue
            seen.add((name, line))
            # Approximate the body as the next 60 lines: enough to make the
            # complexity signal useful without pretending to know the extent.
            body = "\n".join(cleaned.splitlines()[line - 1 : line + 60])
            parsed.symbols.append(
                Symbol(
                    name=name,
                    kind=kind,
                    file=source.relative,
                    line=line,
                    end_line=line,
                    language=source.language,
                    qualified_name=f"{source.relative}:{name}",
                    signature=match.group(0).strip()[:120],
                    complexity=approximate_complexity(body, rules.branch_keywords),
                    parser="pattern",
                    is_public=not name.startswith("_"),
                )
            )

    for pattern in rules.imports:
        for match in re.finditer(pattern, cleaned):
            target = match.group("target")
            parsed.imports.append(
                ImportEdge(
                    source.relative,
                    target,
                    line_of(match.start()),
                    # A relative specifier can only refer to this repository.
                    internal=target.startswith("."),
                )
            )

    parsed.symbols.sort(key=lambda s: s.line)
    return parsed


def parse_file(source: SourceFile) -> ParsedFile:
    """Parse one file with the best available strategy for its language."""
    if source.language == "python":
        return parse_python(source)
    return parse_with_patterns(source)


def parse_all(files: list[SourceFile]) -> list[ParsedFile]:
    return [parse_file(f) for f in files]
