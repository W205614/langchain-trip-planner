"""Agent filesystem paths, independent of the business database."""
from pathlib import Path
from ..config import get_settings

DATA_DIR = Path(get_settings().data_dir).resolve() if get_settings().data_dir else Path(__file__).resolve().parents[2] / "data"
