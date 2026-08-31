# -*- coding: utf-8 -*-
"""rom_database 관리용 간단한 CLI."""

import argparse
import json

from . import RomDatabase
from .builders import build_compatibility_database, build_metadata_database


def _info() -> int:
    database = RomDatabase()
    payload = {
        "availability": database.availability(),
        "paths": {
            "metadata": str(database.paths.metadata),
            "dat": str(database.paths.dat),
            "compatibility": str(database.paths.compatibility),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _verify() -> int:
    availability = RomDatabase().availability()
    missing = [name for name, available in availability.items() if not available]
    if missing:
        print("누락된 참조 DB: " + ", ".join(missing))
        return 1
    print("rom_database 참조 DB 검증 완료")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="rom_database 참조 DB 관리")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("info", help="기본 DB 경로와 존재 여부 출력")
    subparsers.add_parser("verify", help="기본 DB 파일 존재 여부 검증")

    build_parser = subparsers.add_parser("build", help="재생성 가능한 참조 DB 빌드")
    build_parser.add_argument("target", choices=["metadata", "compatibility"])

    args = parser.parse_args()
    if args.command == "info":
        return _info()
    if args.command == "verify":
        return _verify()
    if args.command == "build":
        if args.target == "metadata":
            build_metadata_database()
        else:
            build_compatibility_database()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
