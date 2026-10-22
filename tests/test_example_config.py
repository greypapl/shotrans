"""config.example.toml 是新格式，读出来正好是默认配置（示例里写的就是默认值，key 留空）。"""
from shotrans import paths
from shotrans.config import Config, load_config


def test_example_is_new_format_defaults():
    p = paths.program_dir() / "config.example.toml"
    text = p.read_text(encoding="utf-8")
    assert "[primary]" in text and "[backup]" in text and "[vllm]" not in text and "[fallback]" not in text
    assert load_config(p) == Config()