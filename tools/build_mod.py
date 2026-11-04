"""Build and install a hero swap mod locally, without Blender.

Run:  python tools/build_mod.py <build_config.json>

The Blender add-on's "Build + Install Mod" button writes build_config.json next to the exported
model (<CSDK>/content/citadel_addons/<addon>/models/heroes_wip/<hero>/build_config.json), so a
build can be re-run from a terminal after editing materials by hand. Every step is local:
Source 2 Viewer CLI, the Reduced CSDK resource compiler (materials) and the CS2 Workshop Tools
resource compiler (the AnimGraph 2 model). Nothing is uploaded or compiled online.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "addon", "hero_swap_tools")


def _load(name):
    spec = importlib.util.spec_from_file_location(f"hero_swap_tools.{name}", os.path.join(PKG, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    import types
    sys.modules.setdefault("hero_swap_tools", types.ModuleType("hero_swap_tools"))
    _load("kv3")
    build = _load("build")
    cfg = json.load(open(sys.argv[1]))
    print("installed" if cfg.get("install", True) else "built", build.build(cfg))
