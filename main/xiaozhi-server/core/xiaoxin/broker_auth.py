from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML

from core.xiaoxin.doorbell_credentials import DoorbellCredential
from core.xiaoxin.doorbell_credentials import DoorbellCredentialStore
from core.xiaoxin.tenant_config import TenantConfig
from core.xiaoxin.tenant_config import load_tenant_config


@dataclass(frozen=True)
class MosquittoAuthExport:
    password_file: Path
    acl_file: Path


def render_acl(tenant: TenantConfig, credentials: list[DoorbellCredential]) -> str:
    service_username = tenant.doorbell.username or f"{tenant.tenant_id}:server"
    lines = [
        f"user {service_username}",
        "topic read device/+/status",
        "topic read device/+/telemetry",
        "topic write device/+/notification",
        "topic write device/+/overview",
        "",
    ]
    for credential in credentials:
        if credential.tenant_id != tenant.tenant_id or credential.status != "active":
            continue
        lines.extend(
            [
                f"user {credential.username}",
                f"topic write {tenant.status_topic(credential.device_id)}",
                f"topic write {tenant.telemetry_topic(credential.device_id)}",
                f"topic read {tenant.notification_topic(credential.device_id)}",
                f"topic read {tenant.overview_topic(credential.device_id)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def password_entries(
    credentials: list[DoorbellCredential],
    service_username: str,
    service_password: str,
) -> list[tuple[str, str]]:
    entries = [(service_username, service_password)]
    for credential in credentials:
        if credential.status == "active":
            entries.append((credential.username, credential.password))
    return entries


def write_mosquitto_password_file(
    path: Path,
    entries: list[tuple[str, str]],
    runner=subprocess.run,
) -> None:
    if not entries:
        raise ValueError("at least one password entry is required")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    try:
        for index, (username, password) in enumerate(entries):
            args = ["mosquitto_passwd", "-b"]
            if index == 0:
                args.extend(["-c", str(tmp_path)])
            else:
                args.append(str(tmp_path))
            args.extend([username, password])
            runner(args, check=True)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


def export_mosquitto_auth(
    tenant: TenantConfig,
    credential_store: DoorbellCredentialStore,
    output_dir: Path,
    *,
    password_writer=write_mosquitto_password_file,
) -> MosquittoAuthExport:
    service_username = tenant.doorbell.username or f"{tenant.tenant_id}:server"
    service_password = tenant.doorbell.password
    if not service_password:
        raise ValueError("xiaoxin_control.doorbell_mqtt.password is required")

    output_dir.mkdir(parents=True, exist_ok=True)
    active_credentials = credential_store.list_active()
    password_file = output_dir / "password_file"
    acl_file = output_dir / "acl_file"
    password_writer(
        password_file,
        password_entries(active_credentials, service_username, service_password),
    )
    write_atomic(acl_file, render_acl(tenant, active_credentials))
    return MosquittoAuthExport(password_file=password_file, acl_file=acl_file)


def _load_config(path: Path) -> dict:
    yaml = YAML()
    with path.open("r", encoding="utf-8") as f:
        return yaml.load(f)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Xiaoxin Mosquitto auth files")
    parser.add_argument("--config", default="data/.config.yaml")
    parser.add_argument("--db", default="data/xiaoxin_doorbell_credentials.db")
    parser.add_argument("--out", default="mosquitto/auth")
    args = parser.parse_args(argv)

    config = _load_config(Path(args.config))
    tenant = load_tenant_config(config)
    result = export_mosquitto_auth(
        tenant,
        DoorbellCredentialStore(Path(args.db)),
        Path(args.out),
    )
    print(f"wrote {result.password_file}")
    print(f"wrote {result.acl_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
