from tools import run_bench


def test_prefers_data_dir_files(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOTRANS_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "术语表.txt").write_text("", encoding="utf-8")
    assert run_bench.config_file() == tmp_path / "config.toml"
    assert run_bench.glossary_file() == tmp_path / "术语表.txt"


def test_falls_back_to_program_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOTRANS_HOME", str(tmp_path / "空"))
    assert run_bench.config_file() == run_bench.ROOT / "config.local.toml"
    assert run_bench.glossary_file() == run_bench.ROOT / "术语表.txt"