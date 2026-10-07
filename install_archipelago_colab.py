"""Run this once in Google Colab before importing ap_shapley."""

import os
import sys
import warnings
import subprocess
from pathlib import Path

warnings.resetwarnings()
warnings.simplefilter("ignore")
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

PROJECT_PATH = Path(__file__).resolve().parent
AP_PATH = Path("/content/Archipelago")
AP_VERSION = "0.6.7"

if not AP_PATH.exists():
    subprocess.run([
        "git", "clone", "-q", "--depth", "1", "--branch", AP_VERSION,
        "https://github.com/ArchipelagoMW/Archipelago.git", str(AP_PATH)
    ], check=True)

    requirements = AP_PATH / "requirements.txt"
    filtered_path = Path("/tmp/ap_requirements.txt")
    with requirements.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    filtered = [
        line for line in lines
        if not line.lower().startswith(("kivy", "kivymd", "pymem"))
    ]
    with filtered_path.open("w", encoding="utf-8") as f:
        f.writelines(filtered)
    subprocess.run([
        sys.executable, "-m", "pip", "install", "-q", "-r", str(filtered_path)
    ], check=True)

os.environ["AP_TEST_WORLDS"] = "oot"

# Keep this project importable even after switching into Archipelago.
if str(PROJECT_PATH) not in sys.path:
    sys.path.insert(0, str(PROJECT_PATH))

os.chdir(AP_PATH)
if str(AP_PATH) not in sys.path:
    sys.path.insert(0, str(AP_PATH))

print(f"Archipelago {AP_VERSION} ready at {AP_PATH}")
