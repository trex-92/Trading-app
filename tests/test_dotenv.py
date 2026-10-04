import os

from bot.config import load_dotenv
from pathlib import Path


def test_loader_handles_comments_blank_values_bom_and_precedence(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("﻿# header\nA_KEY=abc  # trailing\nB_KEY=   # blank\nC_KEY=\"quoted\"\nD_KEY=keep\n", encoding="utf-8")
    monkeypatch.setenv("D_KEY", "from-env")
    for k in ("A_KEY", "B_KEY", "C_KEY"):
        monkeypatch.delenv(k, raising=False)
    load_dotenv(f)
    assert os.environ["A_KEY"] == "abc" and "B_KEY" not in os.environ and os.environ["C_KEY"] == "quoted"
    assert os.environ["D_KEY"] == "from-env"  # real env wins
    for k in ("A_KEY", "B_KEY", "C_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_blank_optional_settings_do_not_crash_broker_setup(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("MOOMOO_SIM_MARKET=   # optional\nMOOMOO_LIMIT_BUFFER_PCT=\n", encoding="utf-8")
    monkeypatch.delenv("MOOMOO_SIM_MARKET", raising=False)
    monkeypatch.delenv("MOOMOO_LIMIT_BUFFER_PCT", raising=False)
    load_dotenv(f)
    assert int(os.getenv("MOOMOO_SIM_MARKET") or 0) == 0
    assert float(os.getenv("MOOMOO_LIMIT_BUFFER_PCT") or 1) == 1


def test_user_id_must_be_a_uuid():
    import pytest
    from bot.config import Config
    base = dict(supabase_url="https://x.supabase.co", supabase_service_key="k")
    with pytest.raises(SystemExit, match="UUID"):
        Config(supabase_user_id="trex-92", **base).validate()
    Config(supabase_user_id="123e4567-e89b-12d3-a456-426614174000", **base).validate()
