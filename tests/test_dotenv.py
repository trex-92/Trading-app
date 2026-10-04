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
