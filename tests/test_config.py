from app.config import load_config


def test_load_config(tmp_path):
    config_file = tmp_path / "models.yaml"
    config_file.write_text(
        """
default_model: small
models:
  small:
    base_url: http://127.0.0.1:9001
    start_cmd: python -m http.server 9001
""",
        encoding="utf-8",
    )

    cfg = load_config(config_file)

    assert cfg.default_model == "small"
    assert "small" in cfg.models
    assert cfg.models["small"].base_url == "http://127.0.0.1:9001"
