import os
import sys
from pathlib import Path

# 确保 sidecar 根目录在 sys.path（tests 能 import dispatch/collect/reconcile/managed_registry）
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
