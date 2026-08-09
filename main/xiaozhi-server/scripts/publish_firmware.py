from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.xiaoxin.firmware_release import (  # noqa: E402
    FirmwareReleaseCatalog,
    FirmwareReleaseError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import a firmware file as an immutable SHA-256 artifact and create "
            "a release. Releases stay drafts unless --publish is explicitly set."
        )
    )
    parser.add_argument(
        "--operation",
        choices=(
            "create",
            "set-state",
            "set-rollout",
            "allow-device",
            "remove-device",
            "observations",
        ),
        default="create",
        help="operator action; default creates an immutable release",
    )
    parser.add_argument("--source", help="path to the built .bin file")
    parser.add_argument("--database", required=True, help="SQLite release catalog path")
    parser.add_argument("--artifact-dir", required=True, help="immutable artifact root")
    parser.add_argument(
        "--public-ota-url",
        default="",
        help="public https://host/xiaoxin/ota/ URL; required for --publish",
    )
    parser.add_argument("--model", help="target device model")
    parser.add_argument("--version", help="target firmware version")
    parser.add_argument("--board-type", default="", help="optional exact board type")
    parser.add_argument(
        "--partition-layout-id",
        default="",
        help="optional exact partition layout identifier",
    )
    parser.add_argument("--channel", default="stable", help="release channel")
    parser.add_argument("--mandatory", action="store_true", help="mark update mandatory")
    parser.add_argument(
        "--min-current-version",
        default="",
        help="minimum currently-running version eligible for this release",
    )
    parser.add_argument("--release-id", default="", help="release id for control operations")
    parser.add_argument("--build-git-sha", default="", help="source build Git SHA")
    parser.add_argument("--esp-idf-version", default="", help="ESP-IDF build version")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="make the release selectable immediately; omitted means draft",
    )
    parser.add_argument(
        "--rollout-percentage",
        type=int,
        default=None,
        help="0-100 deterministic rollout gate; a canary release defaults to 0",
    )
    parser.add_argument(
        "--allow-device",
        action="append",
        default=[],
        help="device id allowed regardless of rollout percentage; repeatable",
    )
    parser.add_argument(
        "--state",
        choices=("draft", "published", "paused", "revoked"),
        help="target state when --operation=set-state",
    )
    parser.add_argument(
        "--allow-insecure-http",
        action="store_true",
        help="development-only: permit an http public OTA URL",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        catalog = FirmwareReleaseCatalog(
            database_path=args.database,
            artifact_dir=args.artifact_dir,
            public_ota_url=args.public_ota_url,
            default_channel=args.channel,
            allow_insecure_http=args.allow_insecure_http,
        )
        if args.operation == "create":
            if not args.source or not args.model or not args.version:
                parser.error("--source, --model, and --version are required to create a release")
            if args.publish and not args.public_ota_url:
                parser.error("--public-ota-url is required with --publish")
            if args.publish and not (
                args.board_type.strip()
                and args.partition_layout_id.strip()
            ):
                parser.error(
                    "--board-type and --partition-layout-id are required with --publish"
                )
            result: object = catalog.create_release_from_file(
                args.source,
                model=args.model,
                version=args.version,
                board_type=args.board_type,
                partition_layout_id=args.partition_layout_id,
                channel=args.channel,
                mandatory=args.mandatory,
                min_current_version=args.min_current_version,
                state="published" if args.publish else "draft",
                release_id=args.release_id or None,
                build_git_sha=args.build_git_sha,
                esp_idf_version=args.esp_idf_version,
                rollout_percentage=args.rollout_percentage,
                allowlisted_device_ids=args.allow_device,
            )
        else:
            if args.operation != "observations" and not args.release_id:
                parser.error("--release-id is required for this operation")
            if args.operation == "set-state":
                if not args.state:
                    parser.error("--state is required with --operation=set-state")
                result = catalog.set_release_state(args.release_id, args.state)
            elif args.operation == "set-rollout":
                if args.rollout_percentage is None:
                    parser.error(
                        "--rollout-percentage is required with --operation=set-rollout"
                    )
                result = catalog.set_rollout_percentage(
                    args.release_id,
                    args.rollout_percentage,
                )
            elif args.operation == "allow-device":
                if not args.allow_device:
                    parser.error(
                        "--allow-device is required with --operation=allow-device"
                    )
                for device_id in args.allow_device:
                    catalog.add_allowlisted_device(args.release_id, device_id)
                result = {
                    "release": asdict(catalog.get_release(args.release_id)),
                    "allowlisted_device_ids": catalog.list_allowlisted_devices(
                        args.release_id
                    ),
                }
            elif args.operation == "remove-device":
                if not args.allow_device:
                    parser.error(
                        "--allow-device is required with --operation=remove-device"
                    )
                removed = {
                    device_id: catalog.remove_allowlisted_device(
                        args.release_id,
                        device_id,
                    )
                    for device_id in args.allow_device
                }
                result = {
                    "release": asdict(catalog.get_release(args.release_id)),
                    "removed": removed,
                    "allowlisted_device_ids": catalog.list_allowlisted_devices(
                        args.release_id
                    ),
                }
            else:
                result = [
                    asdict(observation)
                    for observation in catalog.list_observations(
                        release_id=args.release_id or None
                    )
                ]
    except FirmwareReleaseError as error:
        parser.error(str(error))
    payload = asdict(result) if hasattr(result, "__dataclass_fields__") else result
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
