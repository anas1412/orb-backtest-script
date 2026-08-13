#!/usr/bin/env python3
"""Cross-platform launcher: setup venv + deps, then start the backtester server."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
VENV_PY = VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main() -> None:
    if not VENV_PY.exists():
        print("Creating venv...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)

    print("Installing dependencies...")
    subprocess.run([str(VENV_PY), "-m", "pip", "install", "--no-cache-dir", "-r", str(ROOT / "requirements.txt")], check=True)

    port = os.environ.get("PORT", "8000")
    print(f"Starting server at http://localhost:{port}  (Ctrl+C to stop)")
    subprocess.run(
        [str(VENV_PY), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port],
        cwd=ROOT,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)