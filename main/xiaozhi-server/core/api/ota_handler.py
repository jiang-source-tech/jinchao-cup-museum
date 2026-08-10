import json
import time
import base64
import hashlib
import hmac
import os
import re
import glob
from typing import Dict, List, Tuple
from aiohttp import web

from core.auth import create_auth_manager
from core.firmware_release import (
    FirmwareCheck,
    FirmwareReleaseCatalog,
    FirmwareReleaseError,
)
from config.config_loader import get_project_dir
from core.utils.util import get_local_ip, get_vision_url
from core.api.base_handler import BaseHandler

TAG = __name__

_OTA_REPORT_OUTCOMES = frozenset({"pending", "committed", "rolled_back", "failed"})
_OTA_REPORT_PARTITIONS = frozenset({"ota_0", "ota_1"})


def _safe_basename(filename: str) -> str:
    # Prevent directory traversal
    return os.path.basename(filename)


def _parse_version(ver: str) -> Tuple[int, ...]:
    # conservative parser: split by non-digit, keep numeric parts
    parts = re.findall(r"\d+", ver)
    return tuple(int(p) for p in parts) if parts else (0,)


def _is_higher_version(a: str, b: str) -> bool:
    """Return True if version string a > b (semver-like numeric compare)."""
    ta = _parse_version(a)
    tb = _parse_version(b)
    # compare tuple lexicographically, but allow different lengths
    maxlen = max(len(ta), len(tb))
    for i in range(maxlen):
        ai = ta[i] if i < len(ta) else 0
        bi = tb[i] if i < len(tb) else 0
        if ai > bi:
            return True
        if ai < bi:
            return False
    return False


class OTAHandler(BaseHandler):
    def __init__(
        self,
        config: dict,
        firmware_release_catalog: FirmwareReleaseCatalog | None = None,
    ):
        super().__init__(config)
        release_config = config.get("ota_release")
        self.firmware_release_catalog = firmware_release_catalog
        if self.firmware_release_catalog is None and isinstance(release_config, dict):
            self.firmware_release_catalog = FirmwareReleaseCatalog.from_config(
                config,
                project_dir=get_project_dir(),
            )
        self.legacy_filename_fallback = bool(
            self.firmware_release_catalog
            and self.firmware_release_catalog.legacy_filename_fallback
        )
        auth_config = config["server"].get("auth", {})
        self.auth_enable = auth_config.get("enabled", False)
        # 设备白名单
        self.allowed_devices = set(auth_config.get("allowed_devices", []))
        self.auth = create_auth_manager(config["server"])

        # firmware storage
        self.bin_dir = os.path.join(os.getcwd(), "data", "bin")
        # cache structure: { 'updated_at': timestamp, 'ttl': seconds, 'files_by_model': { model: [(version, filename), ...] } }
        self._bin_cache: Dict = {
            "updated_at": 0,
            "ttl": config.get("firmware_cache_ttl", 30),
            "files_by_model": {},
        }

    async def handle_activate(self, request):
        response = web.Response(status=404, text="")
        self._add_cors_headers(response)
        return response

    def _refresh_bin_cache_if_needed(self):
        now = int(time.time())
        ttl = int(self._bin_cache.get("ttl", 30))
        if now - int(
            self._bin_cache.get("updated_at", 0)
        ) < ttl and self._bin_cache.get("files_by_model"):
            return

        files_by_model: Dict[str, List[Tuple[str, str]]] = {}
        try:
            if not os.path.isdir(self.bin_dir):
                os.makedirs(self.bin_dir, exist_ok=True)

            # match files like model_1.2.3.bin (allow dots, dashes, underscores in model and version)
            pattern = os.path.join(self.bin_dir, "*.bin")
            for path in glob.glob(pattern):
                fname = os.path.basename(path)
                # filename format: {model}_{version}.bin
                m = re.match(r"^(.+?)_([0-9][A-Za-z0-9\.\-_]*)\.bin$", fname)
                if not m:
                    # skip files not conforming to naming rule
                    continue
                model = m.group(1)
                version = m.group(2)
                try:
                    # The compatibility path must not bypass the release version
                    # contract: firmware parses each dotted segment numerically.
                    FirmwareReleaseCatalog._validate_version(version)
                except FirmwareReleaseError:
                    continue
                files_by_model.setdefault(model, []).append((version, fname))

            # sort versions for each model descending
            for model, items in files_by_model.items():
                items.sort(key=lambda it: _parse_version(it[0]), reverse=True)

            self._bin_cache["files_by_model"] = files_by_model
            self._bin_cache["updated_at"] = now
            self.logger.bind(tag=TAG).info(
                f"Firmware cache refreshed: {len(files_by_model)} models"
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"刷新固件缓存失败: {e}")
            # keep previous cache if any

    def generate_password_signature(self, content: str, secret_key: str) -> str:
        """生成MQTT密码签名

        Args:
            content: 签名内容 (clientId + '|' + username)
            secret_key: 密钥

        Returns:
            str: Base64编码的HMAC-SHA256签名
        """
        try:
            hmac_obj = hmac.new(
                secret_key.encode("utf-8"), content.encode("utf-8"), hashlib.sha256
            )
            signature = hmac_obj.digest()
            return base64.b64encode(signature).decode("utf-8")
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"生成MQTT密码签名失败: {e}")
            return ""

    def _get_websocket_url(self, local_ip: str, port: int) -> str:
        """获取websocket地址

        Args:
            local_ip: 本地IP地址
            port: 端口号

        Returns:
            str: websocket地址
        """
        server_config = self.config["server"]
        websocket_config = str(server_config.get("websocket", "")).strip()

        if websocket_config and "你的" not in websocket_config:
            return websocket_config
        return f"ws://{local_ip}:{port}/museum/v1/"

    async def handle_post(self, request):
        """处理 OTA POST 请求

        This handler will:
        - read device id/client id (as before)
        - attempt to determine device model and current firmware version (prefer headers, fallback to body)
        - check data/bin for newer firmware for that model
        - if found a newer firmware, set firmware.url to the download endpoint
        """
        try:
            data = await request.text()
            self.logger.bind(tag=TAG).debug(
                f"OTA request method={request.method} path={request.path}"
            )

            device_id = request.headers.get("device-id", "")
            if device_id:
                device_id = device_id.strip()
                if not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", device_id):
                    return web.json_response(
                        {"success": False, "message": "invalid device_id"},
                        status=400,
                    )
                self.logger.bind(tag=TAG).info(f"OTA请求设备ID: {device_id}")
            else:
                raise Exception("OTA请求设备ID为空")

            client_id = request.headers.get("client-id", "").strip()
            if client_id:
                self.logger.bind(tag=TAG).info(f"OTA请求ClientID: {client_id}")
            else:
                raise Exception("OTA请求ClientID为空")

            data_json = {}
            try:
                data_json = json.loads(data) if data else {}
            except Exception:
                data_json = {}

            server_config = self.config["server"]
            # Distinguish ports:
            # - websocket_port is used to construct websocket URL (server["port"])
            # - http_port is used to construct OTA download URLs (server["http_port"])
            websocket_port = int(server_config.get("port", 8000))
            http_port = int(server_config.get("http_port", 8003))
            local_ip = get_local_ip()

            # Determine the board type independently from the legacy model field.
            board_type = ""
            for h in ("board-type", "board_type"):
                if h in request.headers:
                    board_type = request.headers.get(h, "").strip()
                    break
            if not board_type:
                try:
                    board = data_json.get("board", {})
                    if isinstance(board, dict):
                        board_type = str(board.get("type") or "").strip()
                except Exception:
                    board_type = ""

            # Determine device model (prefer headers).  Keep board.type as the
            # legacy fallback because older firmware only reports that field.
            device_model = ""
            # header candidates
            for h in ("device-model", "device_model", "model"):
                if h in request.headers:
                    device_model = request.headers.get(h, "").strip()
                    break
            # body fallback
            if not device_model:
                try:
                    if "model" in data_json:
                        device_model = data_json.get("model", "")
                    elif board_type:
                        device_model = board_type
                except Exception:
                    device_model = ""
            if not device_model:
                device_model = "default"

            partition_layout_id = ""
            for h in ("partition-layout-id", "partition_layout_id"):
                if h in request.headers:
                    partition_layout_id = request.headers.get(h, "").strip()
                    break
            if not partition_layout_id:
                try:
                    ota_data = data_json.get("ota", {})
                    if isinstance(ota_data, dict):
                        partition_layout_id = str(
                            ota_data.get("partition_layout_id")
                            or ota_data.get("partition-layout-id")
                            or ""
                        ).strip()
                except Exception:
                    partition_layout_id = ""

            firmware_channel = ""
            for h in ("firmware-channel", "release-channel"):
                if h in request.headers:
                    firmware_channel = request.headers.get(h, "").strip()
                    break
            if not firmware_channel:
                try:
                    application = data_json.get("application", {})
                    if isinstance(application, dict):
                        firmware_channel = str(
                            application.get("firmware_channel")
                            or application.get("channel")
                            or ""
                        ).strip()
                except Exception:
                    firmware_channel = ""

            # Determine device current version (prefer headers)
            device_version = ""
            for h in (
                "device-version",
                "device_version",
                "firmware-version",
                "app-version",
                "application-version",
            ):
                if h in request.headers:
                    device_version = request.headers.get(h, "").strip()
                    break
            if not device_version:
                try:
                    device_version = data_json.get("application", {}).get("version", "")
                except Exception:
                    device_version = ""
            if not device_version:
                device_version = "0.0.0"

            # A device may resend a lifecycle report with a later ordinary OTA
            # check. It is audit-only: selection below continues to use only the
            # normal check facts parsed above.
            self._record_ota_report(data_json, device_id)

            return_json = {
                "server_time": {
                    "timestamp": int(round(time.time() * 1000)),
                    "timezone_offset": server_config.get("timezone_offset", 8) * 60,
                },
                "firmware": {
                    "version": device_version,
                    "url": "",
                },
            }
            # existing mqtt/websocket logic (unchanged)
            mqtt_gateway_endpoint = server_config.get("mqtt_gateway")

            if mqtt_gateway_endpoint:  # 如果配置了非空字符串
                # 尝试从请求数据中获取设备型号（已解析 above）
                try:
                    group_id = f"GID_{device_model}".replace(":", "_").replace(" ", "_")
                except Exception as e:
                    self.logger.bind(tag=TAG).error(f"获取设备型号失败: {e}")
                    group_id = "GID_default"

                mac_address_safe = device_id.replace(":", "_")
                mqtt_client_id = f"{group_id}@@@{mac_address_safe}@@@{mac_address_safe}"

                # 构建用户数据
                user_data = {"ip": "unknown"}
                try:
                    user_data_json = json.dumps(user_data)
                    username = base64.b64encode(user_data_json.encode("utf-8")).decode(
                        "utf-8"
                    )
                except Exception as e:
                    self.logger.bind(tag=TAG).error(f"生成用户名失败: {e}")
                    username = ""

                # 生成密码
                password = ""
                signature_key = server_config.get("mqtt_signature_key", "")
                if signature_key:
                    password = self.generate_password_signature(
                        mqtt_client_id + "|" + username, signature_key
                    )
                    if not password:
                        password = ""  # 签名失败则留空，由设备决定是否允许无密码
                else:
                    self.logger.bind(tag=TAG).warning("缺少MQTT签名密钥，密码留空")

                # 构建MQTT配置（直接使用 mqtt_gateway 字符串）
                return_json["mqtt"] = {
                    "endpoint": mqtt_gateway_endpoint,
                    "client_id": mqtt_client_id,
                    "username": username,
                    "password": password,
                    "publish_topic": "device-server",
                    "subscribe_topic": f"devices/p2p/{mac_address_safe}",
                }
                self.logger.bind(tag=TAG).info(f"为设备 {device_id} 下发MQTT网关配置")

            else:  # 未配置 mqtt_gateway，下发 WebSocket
                # 如果开启了认证，则进行认证校验
                token = ""
                if self.auth_enable:
                    if self.allowed_devices:
                        if device_id not in self.allowed_devices:
                            token = self.auth.generate_token(client_id, device_id)
                    else:
                        token = self.auth.generate_token(client_id, device_id)
                # NOTE: use websocket_port here
                return_json["websocket"] = {
                    "url": self._get_websocket_url(local_ip, websocket_port),
                    "token": token,
                }
                self.logger.bind(tag=TAG).info(
                    f"未配置MQTT网关，为设备 {device_id} 下发WebSocket配置"
                )

            # The release catalog is authoritative.  Legacy filename scanning is
            # deliberately opt-in so a mutable data/bin file cannot silently
            # replace a published digest artifact.
            try:
                catalog = self.firmware_release_catalog
                self._record_firmware_observation(
                    device_id=device_id,
                    event="checked",
                    current_version=device_version,
                    result="received",
                    reason="ota_request",
                )
                offer = (
                    catalog.select_offer(
                        FirmwareCheck(
                            device_id=device_id,
                            model=str(device_model),
                            board_type=board_type,
                            partition_layout_id=partition_layout_id,
                            current_version=device_version,
                            channel=firmware_channel or catalog.default_channel,
                        )
                    )
                    if catalog is not None
                    else None
                )
                if offer is not None:
                    return_json["firmware"] = offer.to_firmware_payload()
                    self._record_firmware_observation(
                        device_id=device_id,
                        event="offer",
                        current_version=device_version,
                        release_id=offer.release_id,
                        target_version=offer.version,
                        sha256=offer.sha256,
                        result="eligible",
                        reason="published_release",
                    )
                    self.logger.bind(tag=TAG).info(
                        f"为设备 {device_id} 下发发布固件 {offer.version} release={offer.release_id}"
                    )
                elif self.legacy_filename_fallback:
                    self._apply_legacy_filename_fallback(
                        return_json,
                        device_id=device_id,
                        device_model=str(device_model),
                        device_version=device_version,
                    )
                    if return_json["firmware"].get("url"):
                        self._record_firmware_observation(
                            device_id=device_id,
                            event="offer",
                            current_version=device_version,
                            target_version=str(
                                return_json["firmware"].get("version") or ""
                            ),
                            result="legacy_fallback",
                            reason="legacy_filename_fallback",
                        )
                    else:
                        self._record_firmware_observation(
                            device_id=device_id,
                            event="offer",
                            current_version=device_version,
                            result="not_eligible",
                            reason="no_eligible_release",
                        )
                else:
                    self._record_firmware_observation(
                        device_id=device_id,
                        event="offer",
                        current_version=device_version,
                        result="not_eligible",
                        reason="no_eligible_release",
                    )
                    self.logger.bind(tag=TAG).info(
                        f"设备 {device_id} 没有匹配的发布固件: {device_version}"
                    )
            except FirmwareReleaseError as e:
                self._record_firmware_observation(
                    device_id=device_id,
                    event="offer",
                    current_version=device_version,
                    result="rejected",
                    reason="catalog_rejected",
                )
                self.logger.bind(tag=TAG).warning(
                    f"发布固件选择被拒绝: {e}"
                )
            except Exception as e:
                self._record_firmware_observation(
                    device_id=device_id,
                    event="offer",
                    current_version=device_version,
                    result="error",
                    reason="catalog_error",
                )
                self.logger.bind(tag=TAG).error(f"检查固件版本时出错: {e}")

            response = web.Response(
                text=json.dumps(return_json, separators=(",", ":")),
                content_type="application/json",
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"OTA POST处理异常: {e}")
            return_json = {"success": False, "message": "request error."}
            response = web.Response(
                text=json.dumps(return_json, separators=(",", ":")),
                content_type="application/json",
            )
        finally:
            self._add_cors_headers(response)
            return response

    def _record_firmware_observation(self, **facts: str) -> None:
        """Best-effort audit path that never changes an OTA response.

        The caller intentionally supplies only release protocol facts.  In
        particular, this method never forwards request bodies, auth headers, or
        MQTT credentials into the catalog audit log.
        """

        catalog = self.firmware_release_catalog
        if catalog is None:
            return
        try:
            catalog.record_observation(**facts)
        except FirmwareReleaseError as error:
            self.logger.bind(tag=TAG).warning(
                f"发布固件审计记录被拒绝: {error}"
            )
        except Exception:
            self.logger.bind(tag=TAG).exception("发布固件审计记录失败")

    def _record_ota_report(self, data_json: object, device_id: str) -> None:
        """Record a strictly validated, optional device lifecycle report.

        This consumes an additive request-body field only. It never alters
        compatibility checks, rollout selection, release state, or the OTA
        response, so legacy devices remain unaffected.
        """

        if not isinstance(data_json, dict):
            return
        report = data_json.get("ota_report")
        if report is None:
            return
        if not isinstance(report, dict):
            self.logger.bind(tag=TAG).warning("忽略无效 OTA 状态上报")
            return
        required_fields = {
            "release_id",
            "outcome",
            "running_version",
            "running_partition",
            "sha256",
        }
        if set(report) != required_fields or not all(
            isinstance(report[field], str) for field in required_fields
        ):
            self.logger.bind(tag=TAG).warning("忽略不完整 OTA 状态上报")
            return

        catalog = self.firmware_release_catalog
        if catalog is None:
            return
        release_id = report["release_id"].strip()
        outcome = report["outcome"].strip()
        running_version = report["running_version"].strip()
        running_partition = report["running_partition"].strip()
        sha256 = report["sha256"].strip()
        if (
            outcome not in _OTA_REPORT_OUTCOMES
            or running_partition not in _OTA_REPORT_PARTITIONS
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        ):
            self.logger.bind(tag=TAG).warning("忽略不符合合同的 OTA 状态上报")
            return
        try:
            FirmwareReleaseCatalog._validate_version(running_version)
            release = catalog.get_release(release_id)
            if release.sha256 != sha256:
                raise FirmwareReleaseError("OTA report digest does not match release")
            if outcome == "committed" and running_version != release.version:
                raise FirmwareReleaseError("committed OTA report version does not match")
            idempotency_key = hashlib.sha256(
                "\x00".join(
                    (
                        device_id,
                        release_id,
                        outcome,
                        running_version,
                        running_partition,
                        sha256,
                    )
                ).encode("utf-8")
            ).hexdigest()
            catalog.record_observation(
                release_id=release.release_id,
                device_id=device_id,
                event="device_report",
                current_version=running_version,
                target_version=release.version,
                sha256=sha256,
                slot=running_partition,
                result=outcome,
                reason="ota_report",
                idempotency_key=idempotency_key,
            )
        except FirmwareReleaseError as error:
            self.logger.bind(tag=TAG).warning(
                f"忽略 OTA 状态上报: {error}"
            )
        except Exception:
            self.logger.bind(tag=TAG).exception("OTA 状态上报审计失败")

    def _apply_legacy_filename_fallback(
        self,
        return_json: dict,
        *,
        device_id: str,
        device_model: str,
        device_version: str,
    ) -> None:
        self._refresh_bin_cache_if_needed()
        files_by_model = self._bin_cache.get("files_by_model", {})
        candidates = files_by_model.get(device_model, [])

        self.logger.bind(tag=TAG).info(
            f"查找旧版型号 {device_model} 的固件，找到 {len(candidates)} 个候选"
        )
        for version, filename in candidates:
            if not _is_higher_version(version, device_version):
                continue
            vision_url = get_vision_url(self.config)
            url = vision_url.replace(
                "/mcp/vision/explain",
                f"/museum/ota/download/{filename}",
            )
            return_json["firmware"]["version"] = version
            return_json["firmware"]["url"] = url
            self.logger.bind(tag=TAG).info(
                f"为设备 {device_id} 下发旧版文件名固件 {version} -> {url}"
            )
            return
        self.logger.bind(tag=TAG).info(
            f"设备 {device_id} 固件已是最新: {device_version}"
        )

    async def handle_get(self, request):
        """处理 OTA GET 请求"""
        try:
            server_config = self.config["server"]
            local_ip = get_local_ip()
            # use websocket port for websocket URL
            websocket_port = int(server_config.get("port", 8000))
            websocket_url = self._get_websocket_url(local_ip, websocket_port)
            message = f"OTA接口运行正常，向设备发送的websocket地址是：{websocket_url}"
            response = web.Response(text=message, content_type="text/plain")
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"OTA GET请求异常: {e}")
            response = web.Response(text="OTA接口异常", content_type="text/plain")
        finally:
            self._add_cors_headers(response)
            return response

    async def handle_download(self, request):
        """
        下载固件接口
        URL: /museum/ota/download/{filename}
        - 只允许下载 data/bin 目录下的 .bin 文件
        - filename 必须是 basename 且匹配安全的模式
        """
        try:
            if not self.legacy_filename_fallback:
                raise web.HTTPNotFound(text="legacy firmware download disabled")
            fname = request.match_info.get("filename", "")
            if not fname:
                raise web.HTTPBadRequest(text="filename required")

            # sanitize
            fname = _safe_basename(fname)
            # pattern: allow letters, numbers, dot, underscore, dash
            if not re.match(r"^[A-Za-z0-9\.\-_]+\.bin$", fname):
                raise web.HTTPBadRequest(text="invalid filename")

            file_path = os.path.join(self.bin_dir, fname)
            # ensure realpath is under bin_dir
            file_real = os.path.realpath(file_path)
            bin_dir_real = os.path.realpath(self.bin_dir)
            if (
                not file_real.startswith(bin_dir_real + os.sep)
                and file_real != bin_dir_real
            ):
                raise web.HTTPForbidden(text="forbidden")

            if not os.path.isfile(file_real):
                raise web.HTTPNotFound(text="file not found")

            # use FileResponse to stream file
            resp = web.FileResponse(path=file_real)
        except web.HTTPError as e:
            resp = e
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"固件下载异常: {e}")
            resp = web.Response(text="download error", status=500)
        finally:
            try:
                self._add_cors_headers(resp)
            except Exception:
                pass
            return resp

    async def handle_artifact_download(self, request):
        """Stream a published artifact only through its verified SHA-256 path."""
        try:
            digest = request.match_info.get("sha256", "")
            catalog = self.firmware_release_catalog
            artifact_path = catalog.open_artifact(digest) if catalog is not None else None
            if artifact_path is None:
                response = web.Response(
                    text="firmware artifact not found",
                    status=404,
                )
            else:
                response = web.FileResponse(path=artifact_path)
        except Exception:
            self.logger.bind(tag=TAG).exception("published firmware download failed")
            response = web.Response(text="download error", status=500)
        finally:
            self._add_cors_headers(response)
            return response
