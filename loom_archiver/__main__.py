from __future__ import annotations

import argparse
from pathlib import Path

from .config import Config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loom-archiver")
    sub = parser.add_subparsers(dest="command", required=True)
    for cmd in ("auth", "inventory", "run"):
        sp = sub.add_parser(cmd)
        sp.add_argument("--dest", required=True, help="Destination root for the archive")
        sp.add_argument("--floor", type=int, default=None, help="Disk floor in GB")
        sp.add_argument("--delay", type=float, default=None, help="Delay between requests")
        sp.add_argument("--max-depth", type=int, default=10,
                         help="Maximum folder nesting depth to walk (default: 10)")
        sp.add_argument("--folders-only", action="store_true",
                         help="Only archive videos inside folders (skips loose videos)")
    return parser


def _config_from_args(ns) -> Config:
    cfg = Config.default(Path(ns.dest))
    if ns.floor is not None:
        cfg.disk_floor_gb = ns.floor
    if ns.delay is not None:
        cfg.request_delay = ns.delay
    if ns.max_depth is not None:
        cfg.max_folder_depth = ns.max_depth
    cfg.folders_only = ns.folders_only
    return cfg


def main(argv=None) -> int:
    ns = build_parser().parse_args(argv)
    cfg = _config_from_args(ns)

    if ns.command == "auth":
        from .auth import login
        login(cfg)
    elif ns.command == "inventory":
        from . import graphql, diskguard, folders, inventory
        api = None
        try:
            try:
                api = graphql.LoomGraphQL(graphql.load_cookie_header(cfg.auth_state_path), cfg.loom_web_version)
                inventory.run_inventory(api, cfg)
            except (graphql.AuthError, diskguard.DiskFullError, diskguard.MountError,
                    folders.FolderWalkError) as e:
                print(str(e))
                return 1
        finally:
            close = getattr(api, "close", None) if api is not None else None
            if callable(close):
                close()
    elif ns.command == "run":
        from .run import run
        from . import graphql, diskguard, folders, preflight
        try:
            run(cfg)
        except (graphql.AuthError, diskguard.DiskFullError, diskguard.MountError,
                folders.FolderWalkError, preflight.MissingDependencyError) as e:
            print(str(e))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
