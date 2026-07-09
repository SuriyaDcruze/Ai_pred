"""Package the project into ``aegis_project.zip`` for uploading to Colab.

    python -m app.scripts.package_colab
"""

from __future__ import annotations

import os
import zipfile

EXCLUDE_DIRS = {
    "__pycache__", ".venv", "venv", "artifacts", "runs", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".git", "colab", "node_modules", "pdf", "data",
}
EXCLUDE_EXT = {".pt", ".ckpt", ".log", ".pyc"}
OUT = "aegis_project.zip"


def main() -> None:
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirs, files in os.walk("."):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for f in files:
                if os.path.splitext(f)[1] in EXCLUDE_EXT or f == OUT:
                    continue
                path = os.path.join(dirpath, f)
                z.write(path, os.path.join("aegis", os.path.relpath(path, ".")))
                n += 1
    print(f"Packaged {n} files -> {OUT}")


if __name__ == "__main__":
    main()
