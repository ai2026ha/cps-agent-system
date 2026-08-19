from pathlib import Path


def test_v97_migration_does_not_use_integer_boolean_literals():
    source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert "BOOLEAN DEFAULT 0" not in source
    assert "reward_required = 0 WHERE" not in source
    assert 'false_literal = "FALSE" if is_postgres else "0"' in source


def test_v97_release_markers_present():
    project = Path(__file__).resolve().parents[2]
    assert "V108" in (project / "DEPLOY_VERSION.txt").read_text(encoding="utf-8")
    assert "V108" in (project / "backend" / "DEPLOY_VERSION.txt").read_text(encoding="utf-8")
