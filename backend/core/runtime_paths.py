import os
from pathlib import Path


def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".autocms_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _pick_data_root() -> Path:
    env_root = os.getenv("AUTOCMS_DATA_DIR", "").strip()
    repo_fallback = Path(__file__).resolve().parents[2] / ".runtime"
    candidates = [Path(env_root)] if env_root else []
    candidates += [Path("/app"), repo_fallback]
    for candidate in candidates:
        if _is_writable_dir(candidate):
            return candidate
    raise RuntimeError("无法找到可写的数据目录，请设置 AUTOCMS_DATA_DIR")


DATA_ROOT = _pick_data_root()
TMP_UPLOAD_DIR = DATA_ROOT / "tmp_uploads"
LANDING_PAGES_DIR = DATA_ROOT / "landing_pages"

TMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
LANDING_PAGES_DIR.mkdir(parents=True, exist_ok=True)
