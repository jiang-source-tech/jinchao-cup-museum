import json
import time
import base64
import hashlib
import hmac
import re
from aiohttp import web

from core.auth import create_auth_manager
from core.firmware_release import (
    FirmwareCheck,
    FirmwareReleaseCatalog,
    FirmwareReleaseError,
)
from config.config_loader import get_project_dir
from core.utils.util import get_local_ip
from core.api.base_handler import BaseHandler

TAG = __name__

_OTA_REPORT_OUTCOMES = frozenset({"pending", "committed", "rolled_back", "failed"})
_OTA_REPORT_PARTITIONS = frozenset({"ota_0", "ota_1"})


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
        auth_config = config["server"].get("auth", {})
        self.auth_enable = auth_config.get("enabled", False)
        # 设备白名单
        self.allowed_devices = set(auth_config.get("allowed_devices", []))
        self.auth = create_auth_manager(config["server"])

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
        - determine the device release facts from the request
        - select an explicitly published, digest-addressed firmware release
        - return the selected artifact URL when the device is eligible
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
            websocket_port = int(server_config.get("port", 8000))
            local_ip = get_local_ip()

            # Determine the board type independently from the device model.
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

            # Determine device model (prefer headers).
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

            # The release catalog is authoritative. Firmware is offered only from
            # an explicitly published, digest-addressed release.
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
        release selection, release state, or the OTA response.
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
