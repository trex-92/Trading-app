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
    assert os.environ["A_KEY"] == "abc" and os.environ["B_KEY"] == "" and os.environ["C_KEY"] == "quoted"
    assert os.environ["D_KEY"] == "from-env"  # real env wins
    for k in ("A_KEY", "B_KEY", "C_KEY"):
        monkeypatch.delenv(k, raising=False)
