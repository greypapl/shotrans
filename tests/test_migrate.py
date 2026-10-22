"""旧配置迁移：转新格式、key 加密、复制术语表和历史、旧文件不动、失败不写。全部用临时目录里的假旧文件。"""
import logging

from shotrans.config import ServiceConfig, load_config
from shotrans.migrate import MigrateResult, migrate_legacy, needs_migration

LEGACY = ('[vllm]\nbase_url = "http://10.0.0.1:9000/v1"\napi_key = "sk-legacy-7777"\n'
          '[fallback]\nbase_url = "https://api.example.com/v1"\napi_key = "sk-fb-8888"\nmodel = "m1"\n'
          '[hotkey]\ncapture = "Ctrl+Alt+W"\n[tencent]\nenabled = false\n')


def _setup(tmp_path, legacy_text=LEGACY, home_name="home"):
    legacy = tmp_path / "prog"
    legacy.mkdir()
    (legacy / "config.local.toml").write_text(legacy_text, encoding="utf-8")
    return tmp_path / home_name, legacy


def test_needs_migration(tmp_path):
    home, legacy = _setup(tmp_path)
    assert needs_migration(home, legacy) is True
    home.mkdir()
    (home / "config.toml").write_text("", encoding="utf-8")
    assert needs_migration(home, legacy) is False


def test_no_legacy_no_migration(tmp_path):
    assert needs_migration(tmp_path / "home", tmp_path / "空") is False


def test_migrate_converts_and_encrypts(tmp_path):
    home, legacy = _setup(tmp_path)
    r = migrate_legacy(home, legacy, autostart_on=True)
    assert r == MigrateResult("migrated", "", ())
    c = load_config(home / "config.toml")
    assert c.primary == ServiceConfig(preset="custom", base_url="http://10.0.0.1:9000/v1", api_key="sk-legacy-7777",
                                      model="", thinking_off=True, thinking_style="chat_template_kwargs")
    assert c.backup == ServiceConfig(preset="custom", base_url="https://api.example.com/v1", api_key="sk-fb-8888",
                                     model="m1", thinking_off=False)
    assert c.hotkey == "Ctrl+Alt+W" and c.tencent_enabled is False and c.autostart is True
    assert (c.connect_timeout, c.first_token_timeout, c.stall_timeout) == (2.0, 3.0, 10.0)  # 旧超时原样写进新文件
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "sk-legacy-7777" not in text and "sk-fb-8888" not in text


def test_autostart_unknown_stays_none(tmp_path):
    home, legacy = _setup(tmp_path)
    migrate_legacy(home, legacy)
    assert load_config(home / "config.toml").autostart is None


def test_legacy_file_untouched(tmp_path):
    home, legacy = _setup(tmp_path)
    f = legacy / "config.local.toml"
    before, mtime = f.read_bytes(), f.stat().st_mtime_ns
    migrate_legacy(home, legacy)
    assert f.read_bytes() == before and f.stat().st_mtime_ns == mtime


def test_copies_glossary_and_history(tmp_path):
    home, legacy = _setup(tmp_path)
    (legacy / "术语表.txt").write_text("PHQ-8\n", encoding="utf-8")
    (legacy / "history.json").write_text('[{"id": 1}]', encoding="utf-8")
    r = migrate_legacy(home, legacy)
    assert r.copied == ("术语表.txt", "history.json")
    assert (home / "术语表.txt").read_text(encoding="utf-8") == "PHQ-8\n"
    assert (home / "history.json").read_text(encoding="utf-8") == '[{"id": 1}]'
    assert (legacy / "术语表.txt").exists() and (legacy / "history.json").exists()


def test_does_not_overwrite_existing_glossary(tmp_path):
    home, legacy = _setup(tmp_path)
    home.mkdir()
    (home / "术语表.txt").write_text("新\n", encoding="utf-8")
    (legacy / "术语表.txt").write_text("旧\n", encoding="utf-8")
    r = migrate_legacy(home, legacy)
    assert (home / "术语表.txt").read_text(encoding="utf-8") == "新\n"
    assert "术语表.txt" not in r.copied


def test_skipped_when_config_exists(tmp_path):
    home, legacy = _setup(tmp_path)
    home.mkdir()
    (home / "config.toml").write_text("", encoding="utf-8")
    assert migrate_legacy(home, legacy).status == "skipped"
    assert (home / "config.toml").read_text(encoding="utf-8") == ""


def test_skipped_without_legacy(tmp_path):
    r = migrate_legacy(tmp_path / "home", tmp_path / "空")
    assert r.status == "skipped" and not (tmp_path / "home" / "config.toml").exists()


def test_bad_legacy_fails_without_writing(tmp_path):
    home, legacy = _setup(tmp_path, "[vllm\n")
    r = migrate_legacy(home, legacy)
    assert r.status == "failed" and "格式错误" in r.reason
    assert not (home / "config.toml").exists()


def test_invalid_legacy_value_fails(tmp_path):
    home, legacy = _setup(tmp_path, '[hotkey]\ncapture = "Q"\n')
    r = migrate_legacy(home, legacy)
    assert r.status == "failed" and "[hotkey] capture" in r.reason
    assert not (home / "config.toml").exists()


def test_failure_reason_never_contains_key(tmp_path):
    home, legacy = _setup(tmp_path, "[vllm]\napi_key = 12345678\n")
    r = migrate_legacy(home, legacy)
    assert r.status == "failed" and "12345678" not in r.reason


def test_migrate_into_chinese_home(tmp_path):
    home, legacy = _setup(tmp_path, home_name="用户 张三")
    home = home / "截图翻译"
    assert migrate_legacy(home, legacy).status == "migrated"
    assert load_config(home / "config.toml").hotkey == "Ctrl+Alt+W"


def test_migration_logged_without_key(tmp_path, caplog):
    home, legacy = _setup(tmp_path)
    with caplog.at_level(logging.INFO):
        migrate_legacy(home, legacy)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "迁移" in text and "sk-legacy-7777" not in text


# ---------- 评审第 1 轮：复制失败如实上报、config.toml 最后写、旧配置坏了照样复制数据 ----------
from shotrans import migrate as migrate_mod  # noqa: E402
from shotrans.config import ConfigError  # noqa: E402
from shotrans.config import write_config as write_config_real  # noqa: E402


def _with_data(legacy):
    (legacy / "术语表.txt").write_text("PHQ-8\n", encoding="utf-8")
    (legacy / "history.json").write_text('[{"id": 1}]', encoding="utf-8")


def test_copy_failure_reported(tmp_path, monkeypatch):
    home, legacy = _setup(tmp_path)
    _with_data(legacy)
    real = migrate_mod.shutil.copyfile

    def copyfile(s, d):
        if "术语表" in str(s):
            raise OSError("磁盘满")
        return real(s, d)

    monkeypatch.setattr(migrate_mod.shutil, "copyfile", copyfile)
    r = migrate_legacy(home, legacy)
    assert r.status == "migrated" and r.failed_copies == ("术语表.txt",) and r.copied == ("history.json",)
    assert not (home / "术语表.txt").exists() and not (home / "术语表.txt.tmp").exists()
    assert r.legacy == str(legacy)


def test_replace_failure_reported_and_no_partial_files(tmp_path, monkeypatch):
    home, legacy = _setup(tmp_path)
    _with_data(legacy)

    real = migrate_mod.os.replace

    def replace(a, b):  # os 是同一个模块：write_config 也用它，只让数据文件的替换失败
        if b.name == "config.toml":
            return real(a, b)
        raise OSError("被占用")

    monkeypatch.setattr(migrate_mod.os, "replace", replace)
    r = migrate_legacy(home, legacy)
    assert r.failed_copies == ("术语表.txt", "history.json") and r.copied == ()
    assert sorted(p.name for p in home.iterdir()) == ["config.toml"]  # 没有半个文件，也没有残留的 .tmp


def test_data_copied_before_config(tmp_path, monkeypatch):
    home, legacy = _setup(tmp_path)
    _with_data(legacy)
    seen = []
    real = migrate_mod.write_config
    monkeypatch.setattr(migrate_mod, "write_config",
                        lambda p, c: (seen.append(sorted(x.name for x in home.iterdir())), real(p, c)))
    assert migrate_legacy(home, legacy).status == "migrated"
    assert seen == [["history.json", "术语表.txt"]]  # 写 config.toml 时数据已经到位


def test_config_write_failure_retries_next_start(tmp_path, monkeypatch):
    home, legacy = _setup(tmp_path)
    _with_data(legacy)

    def boom(p, c):
        raise ConfigError("保存配置失败（OSError：磁盘满），原文件没有改动")

    monkeypatch.setattr(migrate_mod, "write_config", boom)
    r = migrate_legacy(home, legacy)
    assert r.status == "failed" and "磁盘满" in r.reason and r.copied == ("术语表.txt", "history.json")
    assert not (home / "config.toml").exists()
    assert needs_migration(home, legacy) is True  # 已复制的文件不会让下次跳过迁移
    monkeypatch.setattr(migrate_mod, "write_config", write_config_real)
    r2 = migrate_legacy(home, legacy)
    assert r2.status == "migrated" and r2.copied == () and (home / "config.toml").exists()
    assert (home / "术语表.txt").read_text(encoding="utf-8") == "PHQ-8\n"


def test_bad_legacy_still_copies_data(tmp_path):
    home, legacy = _setup(tmp_path, "[vllm\n")
    _with_data(legacy)
    r = migrate_legacy(home, legacy)
    assert r.status == "failed" and r.copied == ("术语表.txt", "history.json") and r.legacy == str(legacy)
    assert (home / "术语表.txt").read_text(encoding="utf-8") == "PHQ-8\n"
    assert not (home / "config.toml").exists()


def test_readonly_source_copied_as_writable(tmp_path):
    """旧目录里的术语表/历史是只读的：复制过来的副本仍要能写（以后保存术语表、记历史都要写它）。"""
    import os
    import stat

    home, legacy = _setup(tmp_path)
    _with_data(legacy)
    for name in ("术语表.txt", "history.json"):
        os.chmod(legacy / name, stat.S_IREAD)
    try:
        r = migrate_legacy(home, legacy)
        assert r.copied == ("术语表.txt", "history.json")
        for name in ("术语表.txt", "history.json"):
            assert os.access(home / name, os.W_OK)
            (home / name).write_text("改过", encoding="utf-8")  # 真写一次
        assert (home / "术语表.txt").read_text(encoding="utf-8") == "改过"
    finally:
        for name in ("术语表.txt", "history.json"):
            os.chmod(legacy / name, stat.S_IWRITE | stat.S_IREAD)
