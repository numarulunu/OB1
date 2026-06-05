from kontext_v2.config import KontextV2Config


def test_default_config_is_local_and_read_only():
    cfg = KontextV2Config.from_env({})

    assert cfg.service_name == "kontext-v2"
    assert cfg.write_mode == "dry_run"
    assert cfg.database_url == "postgresql://kontext_v2:kontext_v2@localhost:55434/kontext_v2"
