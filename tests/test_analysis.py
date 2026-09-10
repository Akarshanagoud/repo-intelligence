"""Search, architecture, insights and summarisation."""
import pytest

from repointel import find_duplicates, split_identifier, tokenize_code
from repointel.search import TEST_RANK_PENALTY


# --- identifier handling ------------------------------------------------------
@pytest.mark.parametrize(
    "identifier,expected",
    [
        ("validateAuthToken", ["validate", "auth", "token"]),
        ("_check_bearer_cred", ["check", "bearer", "cred"]),
        ("HTTPSConnection", ["https", "connection"]),
        ("parse2FA", ["parse", "2", "fa"]),
        ("simple", ["simple"]),
        ("XMLHttpRequest", ["xml", "http", "request"]),
    ],
)
def test_identifier_splitting(identifier, expected):
    """The single step that makes code search work at all."""
    assert split_identifier(identifier) == expected


def test_tokenizer_keeps_the_whole_identifier_and_its_parts():
    tokens = tokenize_code("validateAuthToken")
    assert "validateauthtoken" in tokens
    assert {"validate", "auth", "token"} <= set(tokens)


def test_language_keywords_are_dropped():
    assert "return" not in tokenize_code("return self.value")


# --- chunking -----------------------------------------------------------------
def test_chunks_align_with_symbols(analysis):
    chunks = [c for c in analysis.index.chunks if c.file == "src/shop/models.py"]
    assert chunks
    assert any(c.symbol.endswith("Product") for c in chunks)


def test_chunk_line_ranges_are_valid(analysis):
    for chunk in analysis.index.chunks:
        assert chunk.start_line >= 1
        assert chunk.end_line >= chunk.start_line
        assert chunk.text.strip()


def test_every_chunk_has_a_checkable_citation(analysis):
    for chunk in analysis.index.chunks[:40]:
        assert ":" in chunk.citation
        assert chunk.citation.startswith(chunk.file)


def test_non_symbol_content_is_still_indexed(analysis):
    """Module docstrings and constants must remain searchable."""
    kinds = {c.kind for c in analysis.index.chunks}
    assert "block" in kinds


# --- search -------------------------------------------------------------------
def test_natural_language_query_finds_the_right_function(analysis):
    hits = analysis.search("validate the auth token", top_k=5)
    assert hits
    assert any("validateAuthToken" in (h.chunk.symbol or "") for h in hits)


def test_query_matching_a_docstring_finds_the_enclosing_symbol(analysis):
    """A method is chunked with its class, so the class is the hit."""
    hits = analysis.search("price after a percentage discount", top_k=5)
    assert any("discounted" in h.chunk.contains for h in hits)


def test_a_method_is_reachable_by_its_own_name(analysis):
    """Jump-to-definition must find methods, not only top-level symbols."""
    matches = analysis.index.find_symbol("discounted")
    assert matches
    assert matches[0].file == "src/shop/models.py"
    assert "discounted" in matches[0].contains


def test_search_reports_both_retrievers(analysis):
    hits = analysis.search("calculate total price", top_k=5)
    assert hits
    assert any("lexical" in h.components or "semantic" in h.components for h in hits)


def test_tests_are_ranked_below_implementation_by_default(analysis):
    hits = analysis.search("calculate total", top_k=6)
    assert hits
    # The implementation must not be displaced by the test that names it.
    assert not hits[0].chunk.is_test


def test_test_penalty_is_recorded_when_applied(analysis):
    hits = analysis.search("calculate total", top_k=10)
    test_hits = [h for h in hits if h.chunk.is_test]
    if test_hits:
        assert test_hits[0].components.get("test_penalty") == TEST_RANK_PENALTY


def test_tests_can_be_excluded_entirely(analysis):
    hits = analysis.search("calculate total", top_k=10, include_tests=False)
    assert all(not h.chunk.is_test for h in hits)


def test_language_filter_applies(analysis):
    hits = analysis.search("cart service", top_k=10, language="typescript")
    assert hits
    assert all(h.chunk.language == "typescript" for h in hits)


def test_path_prefix_filter_applies(analysis):
    hits = analysis.search("handler", top_k=10, path_prefix="service/")
    assert all(h.chunk.file.startswith("service/") for h in hits)


def test_symbol_lookup_prefers_an_exact_match(analysis):
    matches = analysis.index.find_symbol("Product")
    assert matches
    assert matches[0].symbol.rsplit(".", 1)[-1] == "Product"


def test_symbol_lookup_returns_nothing_for_an_unknown_name(analysis):
    assert analysis.index.find_symbol("NoSuchSymbolAnywhere") == []


def test_similarity_finds_the_copy_pasted_function(analysis):
    """The two report builders are near-identical by construction."""
    matches = analysis.index.find_symbol("build_report")
    assert matches
    similar = analysis.index.similar_to(matches[0].chunk_id, limit=3)
    assert similar
    assert any("build_summary" in (h.chunk.symbol or "") for h in similar)


def test_empty_query_returns_nothing_rather_than_everything(analysis):
    assert analysis.search("", top_k=5) == []


# --- architecture -------------------------------------------------------------
def test_the_planted_cycle_is_detected(analysis):
    cycles = analysis.graph.find_cycles()
    assert cycles
    members = {m for cycle in cycles for m in cycle.modules}
    assert "src/shop/orders.py" in members
    assert "src/shop/billing.py" in members


def test_cycle_renders_as_a_readable_path(analysis):
    cycle = analysis.graph.find_cycles()[0]
    rendered = cycle.render()
    assert "->" in rendered
    assert rendered.split(" -> ")[0] == rendered.split(" -> ")[-1]


def test_internal_imports_resolve_to_files(analysis):
    node = analysis.graph.nodes["src/shop/pricing.py"]
    assert "src/shop/models.py" in node.imports


def test_fan_in_identifies_the_hub(analysis):
    hubs = analysis.graph.hubs(3)
    assert hubs
    assert hubs[0].path == "src/shop/models.py"
    assert hubs[0].fan_in >= 2


def test_instability_is_between_zero_and_one(analysis):
    for node in analysis.graph.nodes.values():
        assert 0.0 <= node.instability <= 1.0


def test_stdlib_is_not_reported_as_a_third_party_dependency(analysis):
    """`errors`, `json` and friends tell a reader nothing about the project."""
    assert not ({"json", "typing", "pathlib", "os", "errors"} & set(analysis.graph.external))
    assert analysis.graph.stdlib, "stdlib imports should still be tracked, just separately"


def test_packages_are_rolled_up(analysis):
    packages = analysis.graph.packages()
    assert "src/shop" in packages
    assert packages["src/shop"]["modules"] > 3


def test_mermaid_diagram_is_well_formed(analysis):
    diagram = analysis.mermaid()
    assert diagram.startswith("graph TD")
    assert "-->" in diagram


def test_modules_in_a_cycle_get_no_layer(analysis):
    layers = analysis.graph.infer_layers()
    assert layers["src/shop/orders.py"] == -1
    assert layers["src/shop/models.py"] == 0


# --- insights -----------------------------------------------------------------
def test_totals_are_computed(analysis):
    totals = analysis.insights.totals()
    assert totals["files"] > 5
    assert totals["symbols"] > 10
    assert 0.0 <= totals["doc_coverage"] <= 1.0


def test_test_symbols_are_excluded_from_doc_coverage(analysis):
    """Counting `test_x` as undocumented public API is a number nobody can fix."""
    assert analysis.insights.test_symbols
    documented_names = {s.name for s in analysis.insights.symbols}
    assert not any(name.startswith("test_") for name in documented_names)


def test_the_planted_complex_function_tops_the_ranking(analysis):
    worst = analysis.insights.most_complex_symbols(1)[0]
    assert worst.name == "calculate_total"
    assert worst.complexity > 10


def test_undocumented_public_api_is_found(analysis):
    undocumented = {s.name for s in analysis.insights.undocumented_public_api()}
    assert "calculate_total" in undocumented


def test_documented_symbols_are_not_reported_as_undocumented(analysis):
    undocumented = {s.name for s in analysis.insights.undocumented_public_api()}
    assert "apply_bulk_discount" not in undocumented


def test_risk_falls_back_to_complexity_without_git(analysis):
    """Churn must be reported as unknown, not silently treated as zero."""
    assert analysis.insights.churn_available is False
    riskiest = analysis.insights.riskiest(1)[0]
    assert riskiest.risk > 0
    assert riskiest.to_dict()["commits"] is None


def test_risk_render_states_that_churn_is_unavailable(analysis):
    assert "no git history available" in analysis.render_insights()


def test_untested_module_heuristic_uses_test_contents(analysis):
    """`repository.py` is named only inside a test body, not in a filename."""
    untested = {
        f.path
        for f in analysis.insights.untested_modules(
            [f.relative for f in analysis.scan_result.test_files],
            analysis.test_contents(),
        )
    }
    assert "src/shop/repository.py" not in untested


def test_duplicate_block_is_found(analysis):
    duplicates = find_duplicates(analysis.scan_result.files)
    assert duplicates
    files = {
        location["file"]
        for entry in duplicates
        for location in entry["locations"]
    }
    assert {"src/shop/report_a.py", "src/shop/report_b.py"} <= files


def test_duplicates_ignore_trivial_lines():
    from pathlib import Path

    from repointel.scanner import SourceFile

    trivial = "}\n" * 40
    files = [
        SourceFile(Path("a.js"), "a.js", "javascript", 0, 40, text=trivial),
        SourceFile(Path("b.js"), "b.js", "javascript", 0, 40, text=trivial),
    ]
    assert find_duplicates(files) == []


# --- summarisation ------------------------------------------------------------
def test_summary_states_only_measured_facts(analysis):
    summary = analysis.summary()
    assert summary.source == "template"
    assert str(analysis.insights.totals()["symbols"]) in summary.overview


def test_summary_quotes_existing_docstrings(analysis):
    summary = analysis.summary()
    models = next(
        (m for m in summary.key_modules if m.path == "src/shop/models.py"), None
    )
    assert models is not None
    assert models.purpose.startswith("Domain models")


def test_summary_reports_the_cycle_as_an_observation(analysis):
    observations = " ".join(analysis.summary().observations)
    assert "cycle" in observations.lower()


def test_summary_reports_documentation_coverage(analysis):
    observations = " ".join(analysis.summary().observations)
    assert "docstring" in observations


def test_console_script_entry_point_is_found_but_urls_are_not(analysis):
    """A URL in pyproject.toml must not be reported as an entry point."""
    entries = analysis.summary().entry_points
    assert any("shop/cli.py" in e for e in entries)
    assert not any("https" in e for e in entries)


def test_summary_renders_as_markdown(analysis):
    rendered = analysis.summary().render()
    assert rendered.startswith("# ")
    assert "**Observations**" in rendered


def test_module_without_a_docstring_is_described_by_shape():
    from pathlib import Path

    from repointel.parsing import parse_python
    from repointel.scanner import SourceFile
    from repointel.summarize import summarise_module

    source = SourceFile(
        path=Path("x.py"), relative="x.py", language="python", size=0, lines=4,
        text="class A:\n    pass\n\n\ndef b():\n    pass\n",
    )
    summary = summarise_module(parse_python(source))
    assert "No module docstring" in summary.purpose


# --- end to end ---------------------------------------------------------------
def test_analysis_completes_and_reports_timing(analysis):
    assert analysis.elapsed_ms > 0
    assert analysis.index is not None
    assert len(analysis.parsed) == len(analysis.scan_result.files)


def test_parse_failures_are_surfaced(analysis):
    failures = {p.file for p in analysis.parse_failures}
    assert "src/shop/broken.py" in failures


def test_analysis_serialises(analysis):
    payload = analysis.to_dict()
    assert payload["scan"]["files"] > 5
    assert payload["architecture"]["cycles"]
    assert payload["insights"]["totals"]["symbols"] > 10
