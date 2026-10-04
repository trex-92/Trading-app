"""Builds the self-contained report page from data/research/leaderboard.json (written by research.leaderboard) and research/report_page.html.

    python -m research.build_report [--data PATH] [--fragment PATH] [--html PATH]

Two outputs: a fragment without <html>/<body> tags (what the artifact publisher wraps itself) and a complete HTML file that opens in any
browser (default docs/strategy-search-2026-10.html). The numbers on the page are read from the JSON, never typed in."""
import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def build(data_path: Path, fragment_path: Path | None, html_path: Path | None) -> None:
    data = json.loads(data_path.read_text())
    tpl = (REPO / "research" / "report_page.html").read_text()
    assert "/*__DATA__*/null" in tpl, "template has no data placeholder"
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    fragment = tpl.replace("/*__DATA__*/null", blob)
    if fragment_path:
        fragment_path.parent.mkdir(parents=True, exist_ok=True)
        fragment_path.write_text(fragment)
    if html_path:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                             '<meta name="viewport" content="width=device-width, initial-scale=1">\n</head>\n<body style="margin:0">\n'
                             + fragment + "\n</body>\n</html>\n")
    print(f"built: {len(fragment) / 1024:.0f} KB fragment" + (f", {html_path}" if html_path else ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(REPO / "data" / "research" / "leaderboard.json"))
    ap.add_argument("--fragment", default=None)
    ap.add_argument("--html", default=str(REPO / "docs" / "strategy-search-2026-10.html"))
    a = ap.parse_args()
    build(Path(a.data), Path(a.fragment) if a.fragment else None, Path(a.html) if a.html else None)
