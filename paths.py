"""Where files live: next to the code when run from the project, or - in the packed .exe -
read-only bundled files in the unpack folder and the user's own files in %LOCALAPPDATA%."""
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)  # True inside the PyInstaller .exe
PROJECT = Path(__file__).parent

# files shipped with the program (hero list, recognition templates)
BUNDLED = Path(getattr(sys, "_MEIPASS", PROJECT))
# files the program writes (statistics cache, window position, logs)
USER = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DotaDraftHelper" if FROZEN else PROJECT

CACHE = USER / "cache"
LOGS = USER / "logs"
