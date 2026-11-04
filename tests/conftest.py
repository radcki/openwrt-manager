"""Test setup: import the client package without loading Home Assistant."""

import sys
import types
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent.parent / "custom_components" / "openwrt_manager"

# Stub parent packages so that importing `openwrt_client.*` does not execute the
# integration's __init__.py (which requires Home Assistant).
for name, path in (
    ("custom_components", PACKAGE_DIR.parent),
    ("custom_components.openwrt_manager", PACKAGE_DIR),
):
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules.setdefault(name, module)
