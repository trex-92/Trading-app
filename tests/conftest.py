"""The tests must not depend on a developer's real bot/.env (it holds live settings and the Supabase service-role key).
bot.config loads that file into os.environ the first time it is imported; undo that once, here, before any test builds a Config."""
import os
from pathlib import Path

import bot.config as _config  # noqa: F401  (importing it is what loads bot/.env)


def _keys(path: Path):
    if path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.split(" #", 1)[0].strip()
            if line and not line.startswith("#") and "=" in line:
                yield line.partition("=")[0].strip()


for _path in (Path.cwd() / "bot" / ".env", Path(_config.__file__).with_name(".env")):
    for _key in _keys(_path):
        os.environ.pop(_key, None)
