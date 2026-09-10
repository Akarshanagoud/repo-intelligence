"""CLI commands and exit codes."""
import json

from repointel.cli import main


def _args(sample_repo, *extra):
    return [*extra[:1], str(sample_repo), "--no-git", *extra[1:]]


def test_summary_renders_markdown(sample_repo, capsys):
    assert main(["summary", str(sample_repo), "--no-git"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# ")
    assert "**Observations**" in out
    assert "**Key modules**" in out


def test_summary_json(sample_repo, capsys):
    main(["summary", str(sample_repo), "--no-git", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "template"
    assert payload["key_modules"]


def test_summary_reports_parse_failures(sample_repo, capsys):
    main(["summary", str(sample_repo), "--no-git"])
    assert "could not be parsed" in capsys.readouterr().out


def test_search_finds_a_function(sample_repo, capsys):
    assert main(["search", "validate the auth token", str(sample_repo), "--no-git"]) == 0
    assert "validateAuthToken" in capsys.readouterr().out


def test_search_json(sample_repo, capsys):
    main(["search", "calculate total", str(sample_repo), "--no-git", "--json"])
    hits = json.loads(capsys.readouterr().out)
    assert hits
    assert "citation" in hits[0]


def test_search_language_filter(sample_repo, capsys):
    main([
        "search", "cart", str(sample_repo), "--no-git",
        "--language", "typescript", "--json",
    ])
    hits = json.loads(capsys.readouterr().out)
    assert hits
    assert all(h["language"] == "typescript" for h in hits)


def test_search_can_show_code(sample_repo, capsys):
    main(["search", "calculate total", str(sample_repo), "--no-git", "--show-code"])
    assert "def calculate_total" in capsys.readouterr().out


def test_arch_reports_the_cycle(sample_repo, capsys):
    assert main(["arch", str(sample_repo), "--no-git"]) == 0
    out = capsys.readouterr().out
    assert "import cycles" in out
    assert "orders.py" in out and "billing.py" in out


def test_arch_mermaid(sample_repo, capsys):
    main(["arch", str(sample_repo), "--no-git", "--mermaid"])
    out = capsys.readouterr().out
    assert out.startswith("graph TD")


def test_insights_lists_the_complex_function(sample_repo, capsys):
    assert main(["insights", str(sample_repo), "--no-git"]) == 0
    out = capsys.readouterr().out
    assert "calculate_total" in out
    assert "no git history available" in out


def test_symbol_lookup(sample_repo, capsys):
    assert main(["symbol", "Product", str(sample_repo), "--no-git"]) == 0
    assert "models.py" in capsys.readouterr().out


def test_symbol_lookup_finds_a_method(sample_repo, capsys):
    assert main(["symbol", "discounted", str(sample_repo), "--no-git"]) == 0
    assert "models.py" in capsys.readouterr().out


def test_unknown_symbol_exits_nonzero(sample_repo, capsys):
    assert main(["symbol", "NoSuchThing", str(sample_repo), "--no-git"]) == 1
    assert "no symbol" in capsys.readouterr().out


def test_similar_finds_the_copy_paste(sample_repo, capsys):
    assert main(["similar", "build_report", str(sample_repo), "--no-git"]) == 0
    assert "build_summary" in capsys.readouterr().out


def test_duplicates_command(sample_repo, capsys):
    assert main(["duplicates", str(sample_repo), "--no-git"]) == 0
    out = capsys.readouterr().out
    assert "report_a.py" in out and "report_b.py" in out


def test_files_command_reports_exclusions(sample_repo, capsys):
    assert main(["files", str(sample_repo), "--no-git"]) == 0
    out = capsys.readouterr().out
    assert "skipped:" in out
    assert "by language:" in out


def test_check_fails_on_the_planted_cycle(sample_repo, capsys):
    code = main(["check", str(sample_repo), "--no-git", "--max-cycles", "0"])
    capsys.readouterr()
    assert code == 1


def test_check_passes_when_a_cycle_is_allowed(sample_repo, capsys):
    code = main(["check", str(sample_repo), "--no-git", "--max-cycles", "5"])
    capsys.readouterr()
    assert code == 0


def test_check_fails_on_complexity(sample_repo, capsys):
    code = main(["check", str(sample_repo), "--no-git", "--max-complexity", "5"])
    capsys.readouterr()
    assert code == 1


def test_check_json_reports_the_measurements(sample_repo, capsys):
    main(["check", str(sample_repo), "--no-git", "--json", "--max-cycles", "0"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["cycles"] >= 1
    assert payload["failures"]


def test_version(capsys):
    assert main(["--version"]) == 0
    assert "repointel" in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().out.lower()


def test_missing_path_is_a_usage_error(capsys):
    assert main(["summary", "/no/such/repo/anywhere"]) == 2
    assert "error" in capsys.readouterr().err
