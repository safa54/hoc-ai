from pathlib import Path

from typer.testing import CliRunner

from hoc.cli import app

runner = CliRunner()


def run(tmp_path: Path, *args: str):
    return runner.invoke(app, [*args, "--db", str(tmp_path / "t.db")])


def test_seed_show_snapshot_and_revert_flow(tmp_path):
    assert run(tmp_path, "init").exit_code == 0
    seeded = run(tmp_path, "demo", "seed")
    assert seeded.exit_code == 0, seeded.output
    shown = run(tmp_path, "graph", "show")
    assert "bayes-rule" in shown.output and "confuse-conditional-with-inverse" in shown.output
    assert "[locked]" in shown.output  # approved => locked

    cs_id = run(tmp_path, "changeset", "list").output.split()[0]
    assert run(tmp_path, "snapshot", "create", "seeded").exit_code == 0
    assert "seeded" in run(tmp_path, "snapshot", "list").output
    reverted = run(tmp_path, "changeset", "revert", cs_id)
    assert reverted.exit_code == 0 and "reverted" in reverted.output
    assert "Concepts (0)" in run(tmp_path, "graph", "show").output


def test_domain_errors_become_clean_exit_codes(tmp_path):
    run(tmp_path, "init")
    res = run(tmp_path, "changeset", "revert", "cs_missing")
    assert res.exit_code == 1 and "not found" in res.output


def test_propose_from_file_and_llm_usage(tmp_path):
    f = tmp_path / "cs.json"
    f.write_text(
        '{"author":{"kind":"agent","name":"t"},"rationale":"r",'
        '"ops":[{"op":"add_concept","concept":{"id":"x","name":"X"}}]}'
    )
    res = run(tmp_path, "changeset", "propose", str(f), "--apply")
    assert res.exit_code == 0 and "auto_applied" in res.output
    assert "no LLM calls" in run(tmp_path, "llm", "usage").output
