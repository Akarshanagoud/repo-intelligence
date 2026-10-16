"""The explorer's HTTP API, against the synthetic repository with planted properties."""
import pytest
from fastapi.testclient import TestClient

from repointel.server import create_app


@pytest.fixture(scope="module")
def client(sample_repo):
    return TestClient(create_app(sample_repo))


def test_ui_is_served_and_root_redirects(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/ui/"
    assert "Codebase Explorer" in client.get("/ui/").text


def test_overview_matches_the_scan(client, analysis):
    ov = client.get("/api/overview").json()
    assert ov["scan"]["files"] == analysis.scan_result.summary()["files"]
    assert ov["cycles"] == len(analysis.graph.find_cycles())
    docstrings = " ".join(s.docstring.lower() for p in analysis.parsed for s in p.symbols)
    for suggestion in ov["suggestions"]:
        assert all(word in docstrings for word in suggestion.split()), "suggestions come from the code"


def test_search_returns_hits_with_their_code(client):
    hits = client.get("/api/search", params={"q": "calculate the order total", "kind": "function"}).json()
    assert hits and all(h["kind"] == "function" for h in hits)
    top = hits[0]
    assert top["snippet"]["start"] == top["start_line"]
    assert top["snippet"]["lines"]
    assert client.get("/api/search", params={"q": " "}).json() == []


def test_files_are_served_only_from_the_scan(client):
    files = client.get("/api/files").json()
    path = next(f["path"] for f in files if f["path"].endswith(".py") and f["symbols"])
    body = client.get("/api/file", params={"path": path}).json()
    assert body["symbols"] and body["text"]
    # Present on disk but rejected by the scan, or outside the repository entirely.
    for blocked in ("src/shop/schema_generated.py", "assets/logo.py", "../../etc/passwd", "/etc/passwd"):
        assert client.get("/api/file", params={"path": blocked}).status_code == 404


def test_architecture_layers_follow_the_imports(client):
    data = client.get("/api/architecture").json()
    paths = {n["path"] for n in data["nodes"]}
    layer = {n["path"]: n["layer"] for n in data["nodes"]}
    on_cycle = {m for c in data["cycles"] for m in c["modules"]}
    assert data["edges"]
    for edge in data["edges"]:
        assert edge["source"] in paths and edge["target"] in paths
        if edge["source"] not in on_cycle and edge["target"] not in on_cycle:
            assert layer[edge["source"]] > layer[edge["target"]], "an importer sits above what it imports"
    assert data["mermaid"].startswith("graph")


def test_insights_histogram_counts_every_symbol(client, analysis):
    data = client.get("/api/insights").json()
    assert sum(b["symbols"] for b in data["complexity_histogram"]) == sum(len(p.symbols) for p in analysis.parsed)
    assert data["totals"]["churn_available"] is False


def test_reanalysing_requires_a_real_directory(client, sample_repo):
    assert client.post("/api/analyze", json={"path": str(sample_repo / "does-not-exist")}).status_code == 404
    assert client.post("/api/analyze", json={"path": str(sample_repo)}).json()["scan"]["files"] > 0
