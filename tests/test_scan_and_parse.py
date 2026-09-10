"""Scanning exclusions and symbol extraction."""
import pytest

from repointel import cyclomatic_complexity, parse_file, scan
from repointel.parsing import parse_python
from repointel.scanner import GitignoreRules, is_generated, is_test_file


# --- scanning ----------------------------------------------------------------
def test_source_files_are_found(sample_repo):
    result = scan(sample_repo)
    paths = {f.relative for f in result.files}
    assert "src/shop/models.py" in paths
    assert "web/api.ts" in paths
    assert "service/handler.go" in paths


def test_node_modules_is_never_indexed(sample_repo):
    paths = {f.relative for f in scan(sample_repo).files}
    assert not any(p.startswith("node_modules/") for p in paths)


def test_gitignored_directory_is_excluded(sample_repo):
    paths = {f.relative for f in scan(sample_repo).files}
    assert "ignored_dir/secret.py" not in paths


def test_gitignored_glob_is_excluded(sample_repo):
    paths = {f.relative for f in scan(sample_repo).files}
    assert "keepout.ignored.py" not in paths


def test_build_directory_is_excluded(sample_repo):
    paths = {f.relative for f in scan(sample_repo).files}
    assert not any(p.startswith("build/") for p in paths)


def test_minified_file_is_excluded(sample_repo):
    paths = {f.relative for f in scan(sample_repo).files}
    assert "assets/bundle.min.js" not in paths


def test_generated_file_is_excluded_by_its_header(sample_repo):
    """Detected by content, not by filename - the name looks perfectly normal."""
    paths = {f.relative for f in scan(sample_repo).files}
    assert "src/shop/schema_generated.py" not in paths


def test_binary_file_is_excluded_despite_a_source_extension(sample_repo):
    paths = {f.relative for f in scan(sample_repo).files}
    assert "assets/logo.py" not in paths


def test_exclusions_are_counted_and_reportable(sample_repo):
    result = scan(sample_repo)
    assert result.skipped
    assert sum(result.skipped.values()) > 0


def test_language_filter(sample_repo):
    result = scan(sample_repo, languages=["python"])
    assert result.files
    assert all(f.language == "python" for f in result.files)


def test_tests_are_identified(sample_repo):
    result = scan(sample_repo)
    assert any(f.is_test for f in result.files)
    assert all("test" in f.relative for f in result.test_files)


def test_tests_can_be_excluded(sample_repo):
    result = scan(sample_repo, include_tests=False)
    assert result.test_files == []


def test_scan_summary_counts_lines(sample_repo):
    summary = scan(sample_repo).summary()
    assert summary["files"] > 5
    assert summary["total_lines"] > 50
    assert "python" in summary["languages"]


def test_missing_directory_raises():
    with pytest.raises(FileNotFoundError):
        scan("/definitely/not/a/real/path/xyz")


@pytest.mark.parametrize(
    "relative,name,expected",
    [
        ("tests/test_x.py", "test_x.py", True),
        ("src/api_test.go", "api_test.go", True),
        ("src/foo.spec.ts", "foo.spec.ts", True),
        ("src/models.py", "models.py", False),
    ],
)
def test_test_detection(relative, name, expected):
    assert is_test_file(relative, name) is expected


def test_generated_detection_by_line_length():
    assert is_generated("x" * 5000, 2)
    assert not is_generated("def f():\n    return 1\n", 2)


def test_gitignore_matches_at_any_depth(tmp_path):
    (tmp_path / ".gitignore").write_text("*.log\ncache/\n", encoding="utf-8")
    rules = GitignoreRules(tmp_path)
    assert rules.ignored("deep/nested/thing.log")
    assert rules.ignored("cache", is_dir=True)
    assert not rules.ignored("src/main.py")


def test_gitignore_negation(tmp_path):
    (tmp_path / ".gitignore").write_text("*.py\n!keep.py\n", encoding="utf-8")
    rules = GitignoreRules(tmp_path)
    assert rules.ignored("drop.py")
    assert not rules.ignored("keep.py")


# --- parsing -----------------------------------------------------------------
def test_python_symbols_are_exact(sample_repo):
    source = next(f for f in scan(sample_repo).files if f.relative == "src/shop/models.py")
    parsed = parse_file(source)
    assert parsed.parser == "ast"
    names = {s.name for s in parsed.symbols}
    assert {"Product", "discounted", "MAX_BASKET_SIZE"} <= names


def test_qualified_names_include_the_class(sample_repo):
    source = next(f for f in scan(sample_repo).files if f.relative == "src/shop/models.py")
    parsed = parse_file(source)
    discounted = next(s for s in parsed.symbols if s.name == "discounted")
    assert discounted.qualified_name.endswith("Product.discounted")
    assert discounted.kind == "method"
    assert discounted.parent == "Product"


def test_signatures_are_reconstructed(sample_repo):
    source = next(f for f in scan(sample_repo).files if f.relative == "src/shop/models.py")
    parsed = parse_file(source)
    discounted = next(s for s in parsed.symbols if s.name == "discounted")
    assert discounted.signature == "def discounted(self, percent: float) -> float"


def test_docstrings_are_captured(sample_repo):
    source = next(f for f in scan(sample_repo).files if f.relative == "src/shop/models.py")
    parsed = parse_file(source)
    assert parsed.module_docstring.startswith("Domain models")
    assert next(s for s in parsed.symbols if s.name == "Product").has_docstring


def test_private_symbols_are_marked(sample_repo):
    import ast

    tree = ast.parse("def _helper():\n    pass\n")
    del tree
    from pathlib import Path

    from repointel.scanner import SourceFile

    source = SourceFile(
        path=Path("x.py"), relative="x.py", language="python", size=0, lines=2,
        text="def _helper():\n    pass\n",
    )
    parsed = parse_python(source)
    assert parsed.symbols[0].is_public is False


def test_unparseable_file_is_reported_not_dropped(sample_repo):
    source = next(f for f in scan(sample_repo).files if f.relative == "src/shop/broken.py")
    parsed = parse_file(source)
    assert parsed.parse_error is not None
    assert "line" in parsed.parse_error


def test_typescript_symbols_are_extracted(sample_repo):
    source = next(f for f in scan(sample_repo).files if f.relative == "web/api.ts")
    parsed = parse_file(source)
    names = {s.name for s in parsed.symbols}
    assert {"CartLine", "CartService", "validateAuthToken"} <= names
    assert parsed.parser == "pattern"


def test_go_symbols_are_extracted(sample_repo):
    source = next(f for f in scan(sample_repo).files if f.relative == "service/handler.go")
    parsed = parse_file(source)
    names = {s.name for s in parsed.symbols}
    assert {"Handler", "NewHandler", "Process"} <= names


def test_pattern_parsed_symbols_are_labelled_as_approximate(sample_repo):
    source = next(f for f in scan(sample_repo).files if f.relative == "web/api.ts")
    parsed = parse_file(source)
    assert all(s.parser == "pattern" for s in parsed.symbols)


def test_ast_parsed_symbols_are_labelled_as_exact(sample_repo):
    source = next(f for f in scan(sample_repo).files if f.relative == "src/shop/models.py")
    assert all(s.parser == "ast" for s in parse_file(source).symbols)


def test_commented_out_code_does_not_become_a_symbol():
    from pathlib import Path

    from repointel.scanner import SourceFile

    source = SourceFile(
        path=Path("x.ts"), relative="x.ts", language="typescript", size=0, lines=3,
        text="// export function ghost() {}\nexport function real() {}\n",
    )
    names = {s.name for s in parse_file(source).symbols}
    assert "real" in names
    assert "ghost" not in names


# --- complexity ---------------------------------------------------------------
def test_straight_line_code_has_complexity_one():
    import ast

    assert cyclomatic_complexity(ast.parse("x = 1\ny = 2\n")) == 1


def test_each_branch_adds_one():
    import ast

    source = "def f(a):\n    if a:\n        return 1\n    return 2\n"
    assert cyclomatic_complexity(ast.parse(source)) == 2


def test_boolean_operators_count():
    import ast

    simple = cyclomatic_complexity(ast.parse("def f(a, b):\n    if a:\n        pass\n"))
    compound = cyclomatic_complexity(
        ast.parse("def f(a, b):\n    if a and b:\n        pass\n")
    )
    assert compound == simple + 1


def test_comprehension_filters_count():
    import ast

    plain = cyclomatic_complexity(ast.parse("y = [x for x in z]"))
    filtered = cyclomatic_complexity(ast.parse("y = [x for x in z if x > 0]"))
    assert filtered == plain + 1


def test_the_planted_complex_function_is_detected(sample_repo):
    source = next(f for f in scan(sample_repo).files if f.relative == "src/shop/pricing.py")
    parsed = parse_file(source)
    calculate = next(s for s in parsed.symbols if s.name == "calculate_total")
    simple = next(s for s in parsed.symbols if s.name == "apply_bulk_discount")
    assert calculate.complexity > 10
    assert simple.complexity < 5
