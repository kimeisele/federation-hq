"""OMP execution depth regressions (Issue #49).

Mechanically asserts the project-local OMP adapter configuration and its
compatibility with the known five-role adapter topology:

    root(0) -> hq-director(1) -> hq-operator(2) -> worker(3)

`task.maxRecursionDepth == 3` is the exact required bound so the Operator at
depth 2 retains the task capability to dispatch its four leaf workers, while
workers at depth 3 cannot recursively spawn further agents. No generic graph
engine and no DAG-depth calculation — a simple deterministic test for this
known topology only.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
OMP = REPO_ROOT / ".omp"
AGENTS = OMP / "agents"


def _frontmatter(name: str) -> dict:
    text = (AGENTS / name).read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---\n", 2)[1])
    assert isinstance(meta, dict)
    return meta


def _spawns(name: str) -> list[str]:
    return _frontmatter(name).get("spawns") or []


# ── Project-local OMP configuration ───────────────────────────────────────


def test_omp_project_config_exists():
    assert (OMP / "config.yml").exists()


def test_omp_project_config_max_recursion_depth_exactly_three():
    cfg = yaml.safe_load((OMP / "config.yml").read_text(encoding="utf-8"))
    task = cfg.get("task") or {}
    assert task.get("maxRecursionDepth") == 3


def test_omp_project_config_not_unlimited_not_above_three():
    cfg = yaml.safe_load((OMP / "config.yml").read_text(encoding="utf-8"))
    depth = (cfg.get("task") or {}).get("maxRecursionDepth")
    assert depth == 3  # never -1 (unlimited), never 4+


# ── Known five-role adapter topology compatibility ────────────────────────


def test_director_spawns_exactly_operator():
    assert _spawns("hq-director.md") == ["hq-operator"]


def test_operator_spawns_exactly_four_workers():
    assert sorted(_spawns("hq-operator.md")) == [
        "hq-integrator", "hq-repair", "hq-review", "hq-scout"]


def test_leaf_workers_declare_no_additional_spawns():
    for name in ("hq-scout", "hq-repair", "hq-review", "hq-integrator"):
        assert _spawns(f"{name}.md") == [], name


def test_topology_depth_compatible_with_bound():
    """root(0) -> director(1) -> operator(2) -> worker(3): exactly the
    configured maxRecursionDepth; a leaf worker would need depth 4."""
    depth_director = 1
    depth_operator = 2
    depth_worker = 3
    max_depth = yaml.safe_load((OMP / "config.yml").read_text(encoding="utf-8")) \
        ["task"]["maxRecursionDepth"]
    assert depth_director < depth_operator < depth_worker == max_depth
