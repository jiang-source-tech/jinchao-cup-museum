from __future__ import annotations

import hashlib
import hmac
import json
import ipaddress
import os
import re
from uuid import uuid4
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from aiohttp import ClientSession, ClientTimeout, web

from config import config_loader
from core.api.base_handler import BaseHandler
from core.xiaoxin.control_types import (
    ControlValidationError,
    XiaoxinDeliveryState,
    XiaoxinEvent,
    XiaoxinFailureReason,
    parse_control_event_request,
)
from core.xiaoxin.admin_memory import AdminMemoryQueryService
from core.xiaoxin.companion import (
    CompanionControlCommand,
    CompanionIdempotencyConflict,
    CompanionProjectionRequest,
    CompanionSubjectContext,
    CompanionUnavailableError,
    build_companion_subject_context,
)
from core.xiaoxin.compliance import AgeBand, Capability, ComplianceError
from core.xiaoxin.demo_data_store import XiaoxinDemoDataStore
from core.xiaoxin.dispatcher import DispatcherStoppedError
from core.xiaoxin.identity.models import DEVICE_BOUND, SPEAKER_CONFIRMED
from core.xiaoxin.identity.store import (
    normalize_course_remind_before_min,
    normalize_todo_due_at,
)
from core.xiaoxin.local_time import local_date_text, local_datetime
from core.xiaoxin.voiceprint_registration import (
    VoiceprintRegistrationError,
    VoiceprintRegistrar,
    voiceprint_speaker_id,
)
from core.xiaoxin.network_observation import (
    ip_in_networks,
    is_public_global_unicast,
    observed_public_ip,
    trusted_proxy_networks,
)
from core.xiaoxin.overview.service import OverviewSyncService
from core.xiaoxin.semantic_router import is_existing_tool_turn
from core.xiaoxin.overview.providers import (
    LocationValidationError,
    ProviderDataError,
)
from core.xiaoxin.tenant_config import load_tenant_config, validate_mqtt_topic_segment

SESSION_COOKIE = "xiaoxin_session"
CSRF_COOKIE = "xiaoxin_csrf"
_EVALUATION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _csrf_token(session_token: str) -> str:
    return hashlib.sha256(
        f"xiaoxin-control-csrf:{session_token}".encode("utf-8")
    ).hexdigest()


class XiaoxinControlHandler(BaseHandler):
    def __init__(self, config: dict[str, Any], runtime: Any):
        super().__init__(config)
        self.runtime = runtime
        self.static_path = Path(__file__).with_name("static") / "xiaoxin_control.html"
        self.demo_data_store = XiaoxinDemoDataStore(self._demo_data_path())
        self.voiceprint_registrar = VoiceprintRegistrar(
            self.config.get("voiceprint") or {}
        )
        self.overview_service = getattr(runtime, "overview_service", None)
        if self.overview_service is None and hasattr(runtime, "identity_store"):
            self.overview_service = OverviewSyncService(
                identity_store=runtime.identity_store,
                overview_store=getattr(runtime, "overview_store", None),
                registry=getattr(runtime, "registry", None),
            )
            runtime.overview_service = self.overview_service

    def add_routes(self, app: web.Application) -> None:
        app.add_routes(
            [
                web.get("/xiaoxin/control/", self.handle_console),
                web.post("/api/xiaoxin/auth/register", self.handle_register),
                web.post("/api/xiaoxin/auth/login", self.handle_login),
                web.post("/api/xiaoxin/auth/logout", self.handle_logout),
                web.get("/api/xiaoxin/auth/me", self.handle_me),
                web.post("/api/miniprogram/session", self.handle_miniprogram_session),
                web.get(
                    "/api/miniprogram/compliance/status",
                    self.handle_miniprogram_compliance_status,
                ),
                web.post(
                    "/api/miniprogram/compliance/age-band",
                    self.handle_miniprogram_compliance_age_band,
                ),
                web.post(
                    "/api/miniprogram/compliance/agreements",
                    self.handle_miniprogram_compliance_agreements,
                ),
                web.post(
                    "/api/miniprogram/compliance/settings",
                    self.handle_miniprogram_compliance_settings,
                ),
                web.post(
                    "/api/miniprogram/guardian/invitations",
                    self.handle_miniprogram_guardian_invitation_create,
                ),
                web.get(
                    "/api/miniprogram/guardian/invitations/{token}",
                    self.handle_miniprogram_guardian_invitation,
                ),
                web.post(
                    "/api/miniprogram/guardian/invitations/{token}/accept",
                    self.handle_miniprogram_guardian_invitation_accept,
                ),
                web.post(
                    "/api/miniprogram/guardian/bindings/{binding_id}/revoke",
                    self.handle_miniprogram_guardian_binding_revoke,
                ),
                web.get("/api/miniprogram/profile", self.handle_miniprogram_profile),
                web.patch(
                    "/api/miniprogram/profile",
                    self.handle_miniprogram_profile_update,
                ),
                web.get("/api/miniprogram/device", self.handle_miniprogram_device),
                web.get(
                    "/api/miniprogram/voiceprint",
                    self.handle_miniprogram_voiceprint,
                ),
                web.post(
                    "/api/miniprogram/voiceprint",
                    self.handle_miniprogram_voiceprint_register,
                ),
                web.get(
                    "/api/miniprogram/weather-location",
                    self.handle_miniprogram_weather_location,
                ),
                web.patch(
                    "/api/miniprogram/weather-location",
                    self.handle_miniprogram_weather_location_update,
                ),
                web.post(
                    "/api/miniprogram/device/bind",
                    self.handle_miniprogram_device_bind,
                ),
                web.post(
                    "/api/miniprogram/device/unbind",
                    self.handle_miniprogram_device_unbind,
                ),
                web.get("/api/miniprogram/semester", self.handle_miniprogram_semester),
                web.patch(
                    "/api/miniprogram/semester",
                    self.handle_miniprogram_semester_update,
                ),
                web.get(
                    "/api/miniprogram/course-reminder-settings",
                    self.handle_miniprogram_course_reminder_settings,
                ),
                web.patch(
                    "/api/miniprogram/course-reminder-settings",
                    self.handle_miniprogram_course_reminder_settings_update,
                ),
                web.get("/api/miniprogram/courses", self.handle_miniprogram_courses),
                web.post(
                    "/api/miniprogram/courses",
                    self.handle_miniprogram_course_create,
                ),
                web.get(
                    "/api/miniprogram/courses/{course_id}",
                    self.handle_miniprogram_course,
                ),
                web.patch(
                    "/api/miniprogram/courses/{course_id}",
                    self.handle_miniprogram_course_update,
                ),
                web.delete(
                    "/api/miniprogram/courses/{course_id}",
                    self.handle_miniprogram_course_delete,
                ),
                web.post(
                    "/api/miniprogram/companion/observations",
                    self.handle_miniprogram_companion_observation,
                ),
                web.get(
                    "/api/miniprogram/companion/settings",
                    self.handle_miniprogram_companion_settings,
                ),
                web.get(
                    "/api/miniprogram/companion/history",
                    self.handle_miniprogram_companion_history,
                ),
                web.post(
                    "/api/miniprogram/companion/control",
                    self.handle_miniprogram_companion_control,
                ),
                web.get("/api/miniprogram/todos", self.handle_miniprogram_todos),
                web.post(
                    "/api/miniprogram/todos",
                    self.handle_miniprogram_todo_create,
                ),
                web.patch(
                    "/api/miniprogram/todos/{todo_id}",
                    self.handle_miniprogram_todo_update,
                ),
                web.delete(
                    "/api/miniprogram/todos/{todo_id}",
                    self.handle_miniprogram_todo_delete,
                ),
                web.get(
                    "/api/miniprogram/curriculum/overview",
                    self.handle_miniprogram_curriculum_overview,
                ),
                web.get(
                    "/api/miniprogram/overview",
                    self.handle_miniprogram_overview,
                ),
                web.get(
                    "/api/miniprogram/diagnostics",
                    self.handle_miniprogram_diagnostics,
                ),
                web.get(
                    "/api/miniprogram/notifications/history",
                    self.handle_miniprogram_notification_history,
                ),
                web.post(
                    "/api/xiaoxin/device/location-heartbeat",
                    self.handle_location_heartbeat,
                ),
                web.get("/api/xiaoxin/devices", self.handle_devices),
                web.get("/api/xiaoxin/admin/devices", self.handle_admin_devices),
                web.post("/api/xiaoxin/devices/activation-bind", self.handle_activation_bind_device),
                web.post("/api/xiaoxin/devices/manual-bind", self.handle_manual_bind_device),
                web.post("/api/xiaoxin/devices/wake-by-id", self.handle_wake_device_by_id),
                web.post("/api/xiaoxin/devices/{device_id}/bind", self.handle_bind_device),
                web.post("/api/xiaoxin/devices/{device_id}/unbind", self.handle_unbind_device),
                web.post("/api/xiaoxin/devices/{device_id}/wake", self.handle_wake_device),
                web.post(
                    "/api/xiaoxin/devices/{device_id}/text-chat",
                    self.handle_device_text_chat,
                ),
                web.post(
                    "/api/xiaoxin/devices/{device_id}/overview-sync",
                    self.handle_sync_device_overview,
                ),
                web.post(
                    "/api/xiaoxin/devices/{device_id}/overview-mqtt-sync",
                    self.handle_sync_device_overview_mqtt,
                ),
                web.get("/api/xiaoxin/speakers", self.handle_speakers),
                web.get("/api/xiaoxin/admin/speakers", self.handle_admin_speakers),
                web.patch("/api/xiaoxin/speakers/{speaker_id}", self.handle_update_speaker),
                web.post("/api/xiaoxin/speakers/{speaker_id}/archive", self.handle_archive_speaker),
                web.get("/api/xiaoxin/memory-subjects", self.handle_memory_subjects),
                web.get(
                    "/api/xiaoxin/admin/memory-subjects",
                    self.handle_admin_memory_subjects,
                ),
                web.get(
                    "/api/xiaoxin/admin/memory-subjects/{subject_id}",
                    self.handle_admin_memory_subject_detail,
                ),
                web.post(
                    "/api/xiaoxin/admin/memory-subjects/{subject_id}/control",
                    self.handle_admin_memory_control,
                ),
                web.post(
                    "/api/xiaoxin/admin/memory-subjects/{subject_id}/merge",
                    self.handle_admin_merge_memory_subject,
                ),
                web.get("/api/xiaoxin/admin/audits", self.handle_admin_audits),
                web.get("/api/xiaoxin/demo-data", self.handle_demo_data),
                web.put("/api/xiaoxin/demo-data", self.handle_save_demo_data),
                web.post(
                    "/api/xiaoxin/demo-data/overview/send",
                    self.handle_send_demo_overview,
                ),
                web.post(
                    "/api/xiaoxin/demo-data/notifications/{notification_id}/send",
                    self.handle_send_demo_notification,
                ),
                web.get(
                    "/api/xiaoxin/memory-subjects/{subject_id}/memory",
                    self.handle_memory_subject_detail,
                ),
                web.post(
                    "/api/xiaoxin/memory-subjects/{subject_id}/memory/control",
                    self.handle_companion_memory_control,
                ),
                web.post(
                    "/api/xiaoxin/memory-subjects/{subject_id}/merge",
                    self.handle_merge_memory_subject,
                ),
                web.post("/api/xiaoxin/events", self.handle_create_event),
                web.options("/api/xiaoxin/events", self.handle_options),
                web.get("/api/xiaoxin/deliveries", self.handle_deliveries),
                web.get("/api/xiaoxin/deliveries/{delivery_id}", self.handle_delivery_detail),
            ]
        )

    async def handle_console(self, request: web.Request) -> web.Response:
        response = web.Response(
            text=self.static_path.read_text(encoding="utf-8"),
            content_type="text/html",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        self._add_cors_headers(response)
        return response

    async def handle_register(self, request: web.Request) -> web.Response:
        auth = self._auth_service()
        identity_store = getattr(self.runtime, "identity_store", None)
        if auth is None or identity_store is None:
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        if identity_store.has_admin():
            return self._json(
                {
                    "success": False,
                    "code": "registration_closed",
                    "message": "registration closed",
                },
                status=403,
            )
        try:
            is_local = ipaddress.ip_address(str(request.remote or "")).is_loopback
        except ValueError:
            is_local = False
        if not is_local:
            return self._json(
                {
                    "success": False,
                    "code": "local_registration_required",
                    "message": "local registration required",
                },
                status=403,
            )

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        display_name = str(payload.get("display_name") or username).strip()
        if len(username) < 3:
            return self._json(
                {"success": False, "message": "username too short", "field": "username"},
                status=400,
            )
        if len(password) < 8:
            return self._json(
                {"success": False, "message": "password too short", "field": "password"},
                status=400,
            )
        try:
            user, token = auth.register(
                username,
                password,
                display_name,
                role="admin",
                require_no_admin=True,
            )
        except ValueError as exc:
            if str(exc) == "administrator already exists":
                return self._json(
                    {
                        "success": False,
                        "code": "registration_closed",
                        "message": "registration closed",
                    },
                    status=403,
                )
            return self._json({"success": False, "message": "registration failed"}, status=400)
        except Exception:
            return self._json({"success": False, "message": "registration failed"}, status=400)

        response = self._json({"success": True, "user": self._user_payload(user)})
        self._set_session_cookie(response, token)
        return response

    async def handle_login(self, request: web.Request) -> web.Response:
        auth = self._auth_service()
        if auth is None:
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        result = auth.login(username, password)
        if result is None:
            return self._json({"success": False, "message": "invalid credentials"}, status=401)

        user, token = result
        response = self._json({"success": True, "user": self._user_payload(user)})
        self._set_session_cookie(response, token)
        return response

    async def handle_logout(self, request: web.Request) -> web.Response:
        auth = self._auth_service()
        if auth is None:
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        token = self._session_token(request)
        if token:
            auth.logout(token)
        response = self._json({"success": True})
        response.del_cookie(SESSION_COOKIE, path="/")
        response.del_cookie(CSRF_COOKIE, path="/")
        return response

    async def handle_me(self, request: web.Request) -> web.Response:
        auth = self._auth_service()
        if auth is None:
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        user = self._current_user(request)
        if user is None:
            return self._json({"success": False, "message": "login required"}, status=401)
        return self._json({"success": True, "user": self._user_payload(user)})

    def _trusted_proxy_networks(self) -> tuple[Any, ...]:
        return trusted_proxy_networks(
            self.config,
            warn_invalid=lambda: self.logger.bind(tag="xiaoxin.network").warning(
                "invalid trusted proxy CIDR ignored"
            ),
        )

    @staticmethod
    def _ip_in_networks(address: Any, networks: tuple[Any, ...]) -> bool:
        return ip_in_networks(address, networks)

    @staticmethod
    def _is_public_observation_ip(address: Any) -> bool:
        return is_public_global_unicast(address)

    def _observed_public_ip(self, request: web.Request) -> str | None:
        return observed_public_ip(
            request,
            self.config,
            warn_invalid=lambda: self.logger.bind(tag="xiaoxin.network").warning(
                "invalid trusted proxy CIDR ignored"
            ),
        )

    async def handle_location_heartbeat(self, request: web.Request) -> web.Response:
        device_id = str(request.headers.get("Device-Id") or "").strip()
        username = str(request.headers.get("Device-Username") or "").strip()
        authorization = str(request.headers.get("Authorization") or "")
        password = ""
        if authorization.lower().startswith("bearer "):
            password = authorization.split(" ", 1)[1].strip()

        credential_store = getattr(self.runtime, "doorbell_credential_store", None)
        verify = getattr(credential_store, "verify_password", None)
        if (
            not device_id
            or not username
            or not password
            or not callable(verify)
            or not verify(username, device_id, password)
        ):
            return self._json(
                {"success": False, "message": "invalid device credential"},
                status=401,
            )

        public_ip = self._observed_public_ip(request)
        service = getattr(self.runtime, "overview_service", None)
        observe = getattr(service, "observe_device_ip", None)
        if public_ip is None or not callable(observe):
            return self._json({"success": True, "observed": False})
        try:
            await observe(device_id, public_ip, "location_heartbeat")
        except Exception:
            self.logger.bind(tag="xiaoxin.overview").exception(
                "device IP observation failed reason=location_heartbeat"
            )
            return self._json({"success": True, "observed": False})
        return self._json({"success": True, "observed": True})

    async def handle_miniprogram_session(self, request: web.Request) -> web.Response:
        auth = self._auth_service()
        if auth is None or not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        openid = str(payload.get("openid") or "").strip()
        code = str(payload.get("code") or "").strip()
        nickname = str(payload.get("nickname") or "").strip() or None
        account_role = str(payload.get("accountRole") or "student").strip()
        if account_role not in {"student", "guardian"}:
            return self._json(
                {"success": False, "message": "unsupported account role", "field": "accountRole"},
                status=400,
            )
        if not openid and code:
            try:
                openid = await self._openid_from_miniprogram_code(code)
            except ValueError as exc:
                return self._json(
                    {"success": False, "message": str(exc), "field": "code"},
                    status=400,
                )
        if not openid:
            return self._json(
                {"success": False, "message": "openid required", "field": "openid"},
                status=400,
            )

        compliance = self._compliance_service()
        if compliance is None:
            return self._json(
                {"success": False, "message": "compliance unavailable"},
                status=503,
            )
        try:
            compliance.ensure_miniprogram_account(
                openid,
                account_role=account_role,
                linked_user_id=None,
            )
            if account_role == "guardian":
                user = self.runtime.identity_store.get_or_create_guardian_by_openid(
                    openid,
                    nickname,
                )
                profile = None
            else:
                user, profile = self.runtime.identity_store.get_or_create_student_by_openid(
                    openid,
                    nickname,
                )
            compliance.ensure_miniprogram_account(
                openid,
                account_role=account_role,
                linked_user_id=user.id,
            )
        except (ValueError, ComplianceError) as exc:
            return self._compliance_error_response(exc)

        token = auth.create_session_for_user(user.id)
        response_payload = {
            "success": True,
            "token": token,
            "accountRole": account_role,
            "user": self._user_payload(user),
            "profile": self._student_profile_payload(profile) if profile else None,
        }
        if account_role == "student":
            response_payload["compliance"] = self._compliance_status_payload(
                compliance.status_for_user(user.id)
            )
        return self._json(response_payload)

    async def handle_miniprogram_compliance_status(
        self,
        request: web.Request,
    ) -> web.Response:
        context = self._student_compliance_context(request)
        if isinstance(context, web.Response):
            return context
        service, user = context
        return self._json(
            {
                "success": True,
                "compliance": self._compliance_status_payload(
                    service.status_for_user(user.id)
                ),
            }
        )

    async def handle_miniprogram_compliance_age_band(
        self,
        request: web.Request,
    ) -> web.Response:
        context = self._student_compliance_context(request)
        if isinstance(context, web.Response):
            return context
        service, user = context
        payload = await self._compliance_json_payload(request)
        if isinstance(payload, web.Response):
            return payload
        try:
            age_band = AgeBand(str(payload.get("ageBand") or "").strip())
            status = service.declare_age_band(user.id, age_band)
        except (ValueError, ComplianceError) as exc:
            return self._compliance_error_response(exc)
        return self._json(
            {"success": True, "compliance": self._compliance_status_payload(status)}
        )

    async def handle_miniprogram_compliance_agreements(
        self,
        request: web.Request,
    ) -> web.Response:
        context = self._student_compliance_context(request)
        if isinstance(context, web.Response):
            return context
        service, user = context
        payload = await self._compliance_json_payload(request)
        if isinstance(payload, web.Response):
            return payload
        if payload.get("accepted") is not True:
            return self._json(
                {
                    "success": False,
                    "code": "agreement_required",
                    "message": "accepted must be true",
                },
                status=400,
            )
        status = service.accept_current_agreements(user.id)
        return self._json(
            {"success": True, "compliance": self._compliance_status_payload(status)}
        )

    async def handle_miniprogram_compliance_settings(
        self,
        request: web.Request,
    ) -> web.Response:
        context = self._student_compliance_context(request)
        if isinstance(context, web.Response):
            return context
        service, user = context
        payload = await self._compliance_json_payload(request)
        if isinstance(payload, web.Response):
            return payload
        proactive_enabled = payload.get("proactiveEnabled", False)
        memory_enabled = payload.get("memoryEnabled", False)
        if not isinstance(proactive_enabled, bool) or not isinstance(memory_enabled, bool):
            return self._json(
                {
                    "success": False,
                    "code": "invalid_settings",
                    "message": "settings must be boolean",
                },
                status=400,
            )
        try:
            status = service.update_settings(
                user.id,
                proactive_enabled=proactive_enabled,
                memory_enabled=memory_enabled,
            )
        except ComplianceError as exc:
            return self._compliance_error_response(exc)
        return self._json(
            {"success": True, "compliance": self._compliance_status_payload(status)}
        )

    async def handle_miniprogram_guardian_invitation_create(
        self,
        request: web.Request,
    ) -> web.Response:
        context = self._student_compliance_context(request)
        if isinstance(context, web.Response):
            return context
        service, user = context
        try:
            invitation = service.create_guardian_invitation(user.id)
        except ComplianceError as exc:
            return self._compliance_error_response(exc)
        return self._json(
            {
                "success": True,
                "invitation": {
                    "token": invitation.token,
                    "bindingId": invitation.binding.id,
                    "status": invitation.binding.status,
                    "expiresAt": invitation.binding.expires_at,
                },
            }
        )

    async def handle_miniprogram_guardian_invitation(
        self,
        request: web.Request,
    ) -> web.Response:
        context = self._guardian_compliance_context(request)
        if isinstance(context, web.Response):
            return context
        service, _ = context
        try:
            binding = service.guardian_invitation(request.match_info["token"])
        except ComplianceError as exc:
            return self._compliance_error_response(exc)
        profile = self.runtime.identity_store.get_student_profile_for_user(
            binding.student_user_id
        )
        return self._json(
            {
                "success": True,
                "invitation": {
                    "bindingId": binding.id,
                    "studentNickname": str((profile or {}).get("nickname") or "小芯用户"),
                    "status": binding.status,
                    "expiresAt": binding.expires_at,
                },
            }
        )

    async def handle_miniprogram_guardian_invitation_accept(
        self,
        request: web.Request,
    ) -> web.Response:
        context = self._guardian_compliance_context(request)
        if isinstance(context, web.Response):
            return context
        service, account = context
        payload = await self._compliance_json_payload(request)
        if isinstance(payload, web.Response):
            return payload
        if payload.get("accepted") is not True:
            return self._json(
                {
                    "success": False,
                    "code": "guardian_consent_required",
                    "message": "accepted must be true",
                },
                status=400,
            )
        try:
            status = service.confirm_guardian_invitation(
                token=request.match_info["token"],
                guardian_account=account,
            )
        except ComplianceError as exc:
            return self._compliance_error_response(exc)
        return self._json(
            {"success": True, "compliance": self._compliance_status_payload(status)}
        )

    async def handle_miniprogram_guardian_binding_revoke(
        self,
        request: web.Request,
    ) -> web.Response:
        context = self._student_compliance_context(request)
        if isinstance(context, web.Response):
            return context
        service, user = context
        try:
            status = service.revoke_guardian_binding(
                user.id,
                request.match_info["binding_id"],
            )
        except ComplianceError as exc:
            return self._compliance_error_response(exc)
        return self._json(
            {"success": True, "compliance": self._compliance_status_payload(status)}
        )

    async def _openid_from_miniprogram_code(self, code: str) -> str:
        xiaoxin_config = self.config.get("xiaoxin_control", {})
        code_map = xiaoxin_config.get("miniprogram_code_openid_map") or {}
        mapped_openid = str(code_map.get(code) or "").strip()
        if mapped_openid:
            return mapped_openid

        appid, secret = self._miniprogram_wechat_credentials()
        if not appid or not secret:
            raise ValueError("wechat code exchange unavailable")

        timeout = ClientTimeout(total=5)
        async with ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": appid,
                    "secret": secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            ) as response:
                body = await response.json(content_type=None)

        openid = str(body.get("openid") or "").strip()
        if not openid:
            raise ValueError(str(body.get("errmsg") or "wechat code exchange failed"))
        return openid

    def _miniprogram_wechat_credentials(self) -> tuple[str, str]:
        xiaoxin_config = self.config.get("xiaoxin_control", {})
        appid = str(
            xiaoxin_config.get("miniprogram_appid")
            or xiaoxin_config.get("wechat_appid")
            or os.getenv("XIAOXIN_MINIPROGRAM_APPID")
            or os.getenv("WECHAT_MINIPROGRAM_APPID")
            or os.getenv("WECHAT_APPID")
            or ""
        ).strip()
        secret = str(
            xiaoxin_config.get("miniprogram_secret")
            or xiaoxin_config.get("wechat_secret")
            or os.getenv("XIAOXIN_MINIPROGRAM_SECRET")
            or os.getenv("WECHAT_MINIPROGRAM_SECRET")
            or os.getenv("WECHAT_SECRET")
            or ""
        ).strip()
        return appid, secret

    async def handle_miniprogram_profile(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        profile = self._current_student_profile(request)
        if isinstance(profile, web.Response):
            return profile
        return self._json({"success": True, "profile": self._student_profile_payload(profile)})

    async def handle_miniprogram_profile_update(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        user = request["xiaoxin_user"]
        profile_fields = dict(payload)
        if "academicStatus" in payload and "academic_status" not in payload:
            profile_fields["academic_status"] = payload["academicStatus"]
        profile = self.runtime.identity_store.update_student_profile(
            user.id, profile_fields
        )
        if profile is None:
            return self._json({"success": False, "message": "profile not found"}, status=404)
        academic_keys = {
            "grade",
            "academic_status",
            "academicStatus",
            "major",
            "transition_kind",
            "transitionKind",
            "effective_at",
            "effectiveAt",
            "clear_stage",
            "clearGrade",
        }
        if academic_keys & payload.keys():
            try:
                self._sync_companion_academic_stage(user, profile, update=payload)
            except Exception as exc:
                self.logger.bind(tag="xiaoxin.companion_stage_sync").error(
                    "profile stage sync failed: {}",
                    type(exc).__name__,
                )
                return self._json(
                    {"success": False, "message": "academic profile sync failed"},
                    status=503,
                )
        return self._json({"success": True, "profile": self._student_profile_payload(profile)})

    async def handle_miniprogram_device(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        device = self._miniprogram_bound_device(request)
        return self._json(
            {
                "success": True,
                "device": self._miniprogram_device_payload(device),
            }
        )

    async def handle_miniprogram_voiceprint(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        available = await self.voiceprint_registrar.check_health()
        return self._json(
            {
                "success": True,
                "voiceprint": self._miniprogram_voiceprint_payload(
                    request, available=available
                ),
            }
        )

    async def handle_miniprogram_voiceprint_register(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        user = request["xiaoxin_user"]
        compliance_denied = self._require_compliance_capability(
            user.id,
            Capability.VOICEPRINT_ENROLL,
        )
        if compliance_denied is not None:
            return compliance_denied
        if not self.voiceprint_registrar.configured:
            return self._json(
                {
                    "success": False,
                    "message": "voiceprint service is not configured",
                    "code": "voiceprint_unavailable",
                },
                status=503,
            )

        device = self._miniprogram_bound_device(request)
        if device is None:
            return self._json(
                {"success": False, "message": "device not bound", "code": "device_required"},
                status=409,
            )
        pet = self.runtime.identity_store.get_personal_pet_for_user(user.id)
        if pet is None:
            return self._json(
                {"success": False, "message": "personal pet is not ready", "code": "pet_required"},
                status=409,
            )

        try:
            audio, filename, content_type = await self._read_miniprogram_voiceprint_audio(
                request
            )
            speaker_id = voiceprint_speaker_id(user.id, pet.id)
            await self.voiceprint_registrar.register(
                speaker_id=speaker_id,
                audio=audio,
                filename=filename,
                content_type=content_type,
            )
            student_profile = self.runtime.identity_store.get_student_profile_for_user(user.id)
            display_name = str(student_profile.get("nickname") or "主人") if student_profile else "主人"
            self.runtime.identity_store.get_or_create_speaker_profile(
                owner_user_id=user.id,
                device_id=device.device_id,
                speaker_key=speaker_id,
                display_name=display_name,
                reactivate=True,
            )
        except ValueError as exc:
            return self._json(
                {"success": False, "message": str(exc), "code": "invalid_audio"},
                status=400,
            )
        except VoiceprintRegistrationError as exc:
            return self._json(
                {"success": False, "message": str(exc), "code": "voiceprint_failed"},
                status=502,
            )

        return self._json(
            {
                "success": True,
                "voiceprint": self._miniprogram_voiceprint_payload(
                    request, available=True
                ),
            }
        )

    def _miniprogram_voiceprint_payload(
        self, request: web.Request, *, available: bool | None = None
    ) -> dict[str, Any]:
        configured = self.voiceprint_registrar.configured
        if available is None:
            available = configured
        device = self._miniprogram_bound_device(request)
        user = request["xiaoxin_user"]
        if device is None:
            return {
                "configured": configured,
                "available": available,
                "bound": False,
                "enrolled": False,
                "status": "device_required",
            }
        speaker_id = None
        pet = self.runtime.identity_store.get_personal_pet_for_user(user.id)
        if pet is not None:
            speaker_id = voiceprint_speaker_id(user.id, pet.id)
        profile = next(
            (
                item
                for item in self.runtime.identity_store.list_speakers_for_device(
                    user.id, device.device_id
                )
                if speaker_id and item.speaker_key == speaker_id
            ),
            None,
        )
        enrolled = profile is not None and profile.status != "archived"
        return {
            "configured": configured,
            "available": available,
            "bound": True,
            "enrolled": enrolled,
            "status": (
                "unconfigured"
                if not configured
                else (
                    "unavailable"
                    if not available
                    else ("active" if enrolled else "not_enrolled")
                )
            ),
            "displayName": profile.display_name if profile is not None else "",
        }

    async def _read_miniprogram_voiceprint_audio(
        self, request: web.Request
    ) -> tuple[bytes, str, str]:
        if not request.content_type.startswith("multipart/"):
            raise ValueError("multipart audio is required")
        reader = await request.multipart()
        audio = bytearray()
        found = False
        while True:
            field = await reader.next()
            if field is None:
                break
            if field.name != "audio":
                await field.release()
                continue
            found = True
            while True:
                chunk = await field.read_chunk(size=64 * 1024)
                if not chunk:
                    break
                audio.extend(chunk)
                if len(audio) > 5 * 1024 * 1024:
                    raise ValueError("voiceprint audio is too large")
        if not found or not audio:
            raise ValueError("audio is required")
        if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ValueError("voiceprint audio must be a valid WAV file")
        # Client multipart MIME values vary by platform and are not trustworthy.
        # The upstream service validates the multipart filename extension, so only
        # forward content that passed the RIFF/WAVE signature check with canonical
        # WAV metadata.
        return bytes(audio), "voiceprint.wav", "audio/wav"

    def _weather_location_store(self):
        store = getattr(self.runtime, "overview_store", None)
        if store is not None:
            return store
        service = getattr(self.runtime, "overview_service", None)
        return getattr(service, "overview_store", None)

    def _weather_location_provider(self):
        provider = getattr(self.runtime, "overview_weather_provider", None)
        if provider is not None:
            return provider
        service = getattr(self.runtime, "overview_service", None)
        return getattr(service, "weather_provider", None)

    @staticmethod
    def _validated_place(payload: dict[str, Any], field: str) -> str:
        value = str(payload.get(field) or "").strip()
        if not value:
            raise ValueError(f"{field} required")
        if len(value) > 32 or any(ord(character) < 32 for character in value):
            raise ValueError(f"invalid {field}")
        return value

    def _weather_location_payload(self, user_id: str, device: Any) -> dict[str, Any]:
        store = self._weather_location_store()
        location = store.get_location(device.device_id) if store is not None else None
        service = getattr(self.runtime, "overview_service", None)
        weather = {}
        build_overview = getattr(service, "build_student_overview", None)
        if callable(build_overview):
            overview = build_overview(
                user_id,
                local_date_text(),
                device_id=device.device_id,
            )
            weather = dict(overview.get("weather") or {})
        weather_provider = str(
            getattr(service, "weather_provider_name", "") or ""
        )
        snapshot = store.get_snapshot(device.device_id) if store is not None else None
        return {
            "mode": str((location or {}).get("mode") or "automatic"),
            "province": str((location or {}).get("province") or ""),
            "city": str((location or {}).get("city") or ""),
            "locatedAt": str((location or {}).get("located_at") or ""),
            "weatherDate": str(weather.get("date") or ""),
            "weatherSummary": str(weather.get("summary") or ""),
            "weatherDetail": str(weather.get("detail") or ""),
            "weatherFetchedAt": str(weather.get("fetched_at") or ""),
            "weatherProvider": weather_provider,
            "syncState": str(getattr(snapshot, "publish_state", "unknown")),
            "syncRevision": int(getattr(snapshot, "revision", 0) or 0),
            "lastError": "",
        }

    def _weather_location_device(self, request: web.Request):
        device = self._miniprogram_bound_device(request)
        if device is None:
            return self._json(
                {"success": False, "message": "no bound device"},
                status=404,
            )
        requested_device_id = str(
            request.query.get("deviceId")
            or request.query.get("device_id")
            or ""
        ).strip()
        if requested_device_id and requested_device_id != device.device_id:
            return self._json(
                {"success": False, "message": "device not found"},
                status=404,
            )
        return device

    async def handle_miniprogram_weather_location(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        device = self._weather_location_device(request)
        if isinstance(device, web.Response):
            return device
        user = request["xiaoxin_user"]
        return self._json(
            {
                "success": True,
                "weatherLocation": self._weather_location_payload(user.id, device),
            }
        )

    async def handle_miniprogram_weather_location_update(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        if not isinstance(payload, dict):
            return self._json(
                {"success": False, "message": "json object required", "field": "body"},
                status=400,
            )

        device = self._weather_location_device(request)
        if isinstance(device, web.Response):
            return device
        requested_device_id = str(
            payload.get("deviceId") or payload.get("device_id") or ""
        ).strip()
        if requested_device_id and requested_device_id != device.device_id:
            return self._json(
                {"success": False, "message": "device not found"},
                status=404,
            )

        mode = str(payload.get("mode") or "").strip()
        if mode not in {"automatic", "manual"}:
            return self._json(
                {
                    "success": False,
                    "message": "mode must be automatic or manual",
                    "field": "mode",
                },
                status=400,
            )
        service = getattr(self.runtime, "overview_service", None)
        if getattr(service, "overview_enabled", True) is False:
            return self._json(
                {"success": False, "message": "overview_mqtt_disabled"},
                status=503,
            )
        store = self._weather_location_store()
        if store is None:
            return self._json(
                {"success": False, "message": "weather location unavailable"},
                status=503,
            )

        if mode == "manual":
            try:
                province = self._validated_place(payload, "province")
                city = self._validated_place(payload, "city")
            except ValueError as exc:
                field = "province" if "province" in str(exc) else "city"
                return self._json(
                    {"success": False, "message": str(exc), "field": field},
                    status=400,
                )
            provider = self._weather_location_provider()
            validate_city = getattr(provider, "validate_city", None)
            if not callable(validate_city):
                return self._json(
                    {
                        "success": False,
                        "message": "weather location validation unavailable",
                        "retryable": True,
                    },
                    status=503,
                )
            try:
                await validate_city(province, city)
            except LocationValidationError:
                return self._json(
                    {
                        "success": False,
                        "message": "invalid weather location",
                        "field": "city",
                    },
                    status=400,
                )
            except ProviderDataError:
                return self._json(
                    {
                        "success": False,
                        "message": "weather location validation unavailable",
                        "retryable": True,
                    },
                    status=503,
                )
            except Exception:
                return self._json(
                    {
                        "success": False,
                        "message": "weather location validation unavailable",
                        "retryable": True,
                    },
                    status=503,
                )
            reason = "weather_location_manual"
            service = getattr(self.runtime, "overview_service", None)
            set_manual = getattr(service, "set_manual_location_for_user", None)
            if not callable(set_manual):
                return self._json(
                    {"success": False, "message": "weather location unavailable"},
                    status=503,
                )
            user = request["xiaoxin_user"]
            try:
                result = await set_manual(
                    user.id,
                    device.device_id,
                    province,
                    city,
                    reason,
                )
            except Exception:
                self.logger.bind(tag="xiaoxin.overview").exception(
                    f"overview refresh failed reason={reason}"
                )
                return self._json(
                    {"success": False, "message": "weather location unavailable"},
                    status=503,
                )
            if result is None:
                return self._json(
                    {"success": False, "message": "device not found"},
                    status=404,
                )
            return self._json(
                {
                    "success": True,
                    "weatherLocation": self._weather_location_payload(
                        user.id,
                        device,
                    ),
                }
            )
        else:
            reason = "weather_location_automatic"
            service = getattr(self.runtime, "overview_service", None)
            set_automatic = getattr(
                service,
                "set_automatic_location_for_user",
                None,
            )
            if not callable(set_automatic):
                return self._json(
                    {"success": False, "message": "weather location unavailable"},
                    status=503,
                )
            user = request["xiaoxin_user"]
            try:
                result = await set_automatic(
                    user.id,
                    device.device_id,
                    reason,
                )
            except Exception:
                self.logger.bind(tag="xiaoxin.overview").exception(
                    f"overview refresh failed reason={reason}"
                )
                return self._json(
                    {"success": False, "message": "weather location unavailable"},
                    status=503,
                )
            if result is None:
                return self._json(
                    {"success": False, "message": "device not found"},
                    status=404,
                )
            return self._json(
                {
                    "success": True,
                    "weatherLocation": self._weather_location_payload(
                        user.id,
                        device,
                    ),
                }
            )

    async def handle_miniprogram_device_bind(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        compliance_denied = self._require_compliance_capability(
            request["xiaoxin_user"].id,
            Capability.DEVICE_BIND,
        )
        if compliance_denied is not None:
            return compliance_denied

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        device_id = str(payload.get("device_id") or "").strip()
        display_name = str(payload.get("display_name") or device_id).strip()
        if not device_id:
            return self._json(
                {"success": False, "message": "device_id required", "field": "device_id"},
                status=400,
            )
        try:
            validate_mqtt_topic_segment(device_id, "device_id")
        except ValueError:
            return self._json(
                {"success": False, "message": "invalid device_id"},
                status=400,
            )
        return self._json(
            {"success": False, "message": "activation_required"},
            status=403,
        )

    async def handle_miniprogram_device_unbind(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        device_id = str(payload.get("device_id") or "").strip()
        if not device_id:
            device = self._miniprogram_bound_device(request)
            device_id = device.device_id if device is not None else ""
        if not device_id:
            return self._json({"success": True})

        user = request["xiaoxin_user"]
        unbound = self.runtime.identity_store.unbind_device(device_id, user.id)
        if unbound:
            await self._clear_unbound_device_overview(device_id, "device_unbound")
        return self._json({"success": True})

    async def handle_miniprogram_semester(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        user = request["xiaoxin_user"]
        semester = self.runtime.identity_store.get_student_semester(user.id)
        return self._json(
            {"success": True, "semester": self._student_semester_payload(semester)}
        )

    async def handle_miniprogram_semester_update(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        try:
            payload = json.loads(await request.text())
            self._validate_semester_payload(payload)
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        except (TypeError, ValueError) as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)

        user = request["xiaoxin_user"]
        semester = self.runtime.identity_store.update_student_semester(
            user.id,
            payload,
        )
        await self._refresh_user_overview(user.id, "semester_updated")
        return self._json(
            {"success": True, "semester": self._student_semester_payload(semester)}
        )

    async def handle_miniprogram_course_reminder_settings(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        user = request["xiaoxin_user"]
        settings = self.runtime.identity_store.get_student_course_reminder_settings(
            user.id
        )
        return self._json(
            {
                "success": True,
                "courseReminderSettings": self._course_reminder_settings_payload(
                    settings
                ),
            }
        )

    async def handle_miniprogram_course_reminder_settings_update(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        try:
            payload = json.loads(await request.text())
            self._validate_course_reminder_settings_payload(payload)
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        except (TypeError, ValueError) as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)

        user = request["xiaoxin_user"]
        settings = self.runtime.identity_store.update_student_course_reminder_settings(
            user.id, payload
        )
        return self._json(
            {
                "success": True,
                "courseReminderSettings": self._course_reminder_settings_payload(
                    settings
                ),
            }
        )

    async def handle_miniprogram_courses(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        user = request["xiaoxin_user"]
        courses = self.runtime.identity_store.list_student_courses(user.id)
        return self._json(
            {
                "success": True,
                "courses": [self._student_course_payload(course) for course in courses],
            }
        )

    async def handle_miniprogram_course_create(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        try:
            payload = json.loads(await request.text())
            self._validate_course_payload(payload)
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        except (TypeError, ValueError) as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)

        user = request["xiaoxin_user"]
        course = self.runtime.identity_store.create_student_course(user.id, payload)
        self._observe_course_event(user.id, course, "course_created")
        await self._refresh_user_overview(user.id, "course_created")
        return self._json(
            {"success": True, "course": self._student_course_payload(course)}
        )

    async def handle_miniprogram_course(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        user = request["xiaoxin_user"]
        course_id = str(request.match_info.get("course_id") or "").strip()
        course = self.runtime.identity_store.get_student_course(user.id, course_id)
        if course is None:
            return self._json({"success": False, "message": "course not found"}, status=404)
        return self._json(
            {"success": True, "course": self._student_course_payload(course)}
        )

    async def handle_miniprogram_course_update(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        try:
            payload = json.loads(await request.text())
            self._validate_course_payload(payload)
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        except (TypeError, ValueError) as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)

        user = request["xiaoxin_user"]
        course_id = str(request.match_info.get("course_id") or "").strip()
        course = self.runtime.identity_store.update_student_course(
            user.id,
            course_id,
            payload,
        )
        if course is None:
            return self._json({"success": False, "message": "course not found"}, status=404)
        self._observe_course_event(user.id, course, "course_updated")
        await self._refresh_user_overview(user.id, "course_updated")
        return self._json(
            {"success": True, "course": self._student_course_payload(course)}
        )

    async def handle_miniprogram_course_delete(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        user = request["xiaoxin_user"]
        course_id = str(request.match_info.get("course_id") or "").strip()
        course = self.runtime.identity_store.get_student_course(user.id, course_id)
        deleted = self.runtime.identity_store.delete_student_course(user.id, course_id)
        if deleted and course is not None:
            self._observe_course_event(
                user.id,
                course,
                "course_deleted",
                occurred_at=datetime.now().astimezone().isoformat(),
            )
            await self._refresh_user_overview(user.id, "course_deleted")
        return self._json({"success": True, "deleted": deleted})

    async def handle_miniprogram_todos(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        user = request["xiaoxin_user"]
        todos = self.runtime.identity_store.list_student_todos(user.id)
        return self._json(
            {
                "success": True,
                "todos": [self._student_todo_payload(todo) for todo in todos],
            }
        )

    async def handle_miniprogram_todo_create(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        try:
            payload = json.loads(await request.text())
            if not isinstance(payload, dict):
                raise ValueError("json object required")
            self._validate_todo_payload(payload, partial=False)
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        except (TypeError, ValueError) as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)

        user = request["xiaoxin_user"]
        todo = self.runtime.identity_store.create_student_todo(
            user.id,
            {**payload, "source": "miniprogram", "sourceDeviceId": ""},
        )
        self._observe_todo_event(user.id, todo, "todo_created")
        await self._refresh_user_overview(user.id, "todo_created")
        return self._json(
            {"success": True, "todo": self._student_todo_payload(todo)}
        )

    async def handle_miniprogram_companion_observation(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        compliance_denied = self._require_compliance_capability(
            request["xiaoxin_user"].id,
            Capability.COMPANION_MEMORY_WRITE,
        )
        if compliance_denied is not None:
            return compliance_denied
        ingress = getattr(self.runtime, "observation_ingress", None)
        if ingress is None:
            return self._json(
                {"success": False, "message": "companion observation unavailable"},
                status=503,
            )
        try:
            body = json.loads(await request.text())
            if not isinstance(body, dict):
                raise ValueError("json object required")
            idempotency_key = str(body.get("idempotencyKey") or "").strip()
            if not idempotency_key:
                raise ValueError("idempotencyKey required")
            kind = str(body.get("kind") or "").strip()
            payload = body.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            source_ref, normalized, safe_summary = (
                self._normalize_explicit_companion_observation(kind, payload)
            )
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        except (TypeError, ValueError) as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)

        user = request["xiaoxin_user"]
        observed_at = datetime.now().astimezone().isoformat()
        try:
            result = ingress.observe_user_event(
                user_id=user.id,
                idempotency_key=idempotency_key,
                kind=kind,
                source_kind="miniprogram_companion",
                source_ref=source_ref,
                occurred_at=observed_at,
                payload=normalized,
                safe_summary=safe_summary,
            )
        except CompanionIdempotencyConflict:
            return self._json(
                {
                    "success": False,
                    "message": "idempotency key reused for different content",
                    "field": "idempotencyKey",
                },
                status=409,
            )
        except PermissionError:
            return self._json(
                {"success": False, "message": "confirmed owner required"},
                status=403,
            )
        except CompanionUnavailableError:
            return self._json(
                {"success": False, "message": "companion memory unavailable"},
                status=503,
            )
        except (TypeError, ValueError) as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)
        if result is None:
            return self._json(
                {"success": False, "message": "personal pet unavailable"},
                status=409,
            )
        return self._json(
            {"success": True, "observation": asdict(result)},
            status=202 if result.status == "deferred" else 200,
        )

    async def handle_miniprogram_companion_settings(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        compliance_denied = self._require_compliance_capability(
            request["xiaoxin_user"].id,
            Capability.COMPANION_MEMORY_READ,
        )
        if compliance_denied is not None:
            return compliance_denied
        subject = self._miniprogram_confirmed_subject(request)
        if isinstance(subject, web.Response):
            return subject
        companion_mind = getattr(self.runtime, "companion_mind", None)
        if companion_mind is None:
            return self._json(
                {"success": False, "message": "companion memory unavailable"},
                status=503,
            )
        subject_context = self._companion_subject_context(request, subject)
        if isinstance(subject_context, web.Response):
            return subject_context
        try:
            projection = companion_mind.project(
                CompanionProjectionRequest(
                    subject=subject_context,
                    surface="miniprogram",
                    now=datetime.now().astimezone().isoformat(),
                )
            )
        except CompanionUnavailableError:
            return self._json(
                {"success": False, "message": "companion memory unavailable"},
                status=503,
            )
        return self._json(
            {
                "success": True,
                "memory_subject_id": subject.id,
                "xiaoxin_age": projection.xiaoxin_age,
                "relationship_stage": projection.relationship_stage,
                "settings": projection.payload,
                "preferences": projection.payload.get("companion_preferences", {}),
                "available_actions": (
                    "correct",
                    "forget",
                    "do_not_mention",
                    "too_proactive",
                    "too_personal",
                    "disable_initiative",
                    "set_initiative_quiet_hours",
                ),
            }
        )

    async def handle_miniprogram_companion_control(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        subject = self._miniprogram_confirmed_subject(request)
        if isinstance(subject, web.Response):
            return subject
        try:
            body = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        if not isinstance(body, dict):
            return self._json(
                {"success": False, "message": "json object required", "field": "body"},
                status=400,
            )
        action = str(body.get("action") or "").strip()
        allowed_actions = {
            "correct",
            "forget",
            "do_not_mention",
            "too_proactive",
            "too_personal",
            "disable_initiative",
            "set_initiative_quiet_hours",
        }
        if action not in allowed_actions:
            return self._json(
                {"success": False, "message": "unsupported companion control action", "field": "action"},
                status=400,
            )
        if action == "correct":
            compliance_denied = self._require_compliance_capability(
                request["xiaoxin_user"].id,
                Capability.COMPANION_MEMORY_WRITE,
            )
            if compliance_denied is not None:
                return compliance_denied
        payload = body.get("payload", {})
        if not isinstance(payload, dict):
            return self._json(
                {"success": False, "message": "payload must be an object", "field": "payload"},
                status=400,
            )
        command_payload = dict(payload)
        for key, value in body.items():
            if key not in {"action", "payload", "idempotency_key", "idempotencyKey"}:
                command_payload.setdefault(key, value)
        idempotency_key = str(
            body.get("idempotency_key") or body.get("idempotencyKey") or ""
        ).strip()
        if not idempotency_key:
            user = request["xiaoxin_user"]
            idempotency_key = f"miniprogram:{user.id}:{uuid4().hex}"

        mapped_action = action
        if action == "correct":
            evidence_id = str(command_payload.get("evidence_id") or "").strip()
            replacement = command_payload.get("replacement_content")
            correction = str(command_payload.get("correction") or "").strip()
            if replacement is None and correction:
                replacement = {"canonical_value": correction}
            if not evidence_id or not isinstance(replacement, Mapping):
                return self._json(
                    {"success": False, "message": "correct requires evidence_id and correction", "field": "payload"},
                    status=400,
                )
            command_payload = {
                "evidence_id": evidence_id,
                "replacement_content": dict(replacement),
                "source_summary": str(
                    command_payload.get("source_summary") or "用户纠正了这条记忆"
                ).strip(),
            }
            mapped_action = "correct_evidence"
        elif action == "forget":
            evidence_id = str(command_payload.get("evidence_id") or "").strip()
            theme = str(command_payload.get("theme") or "").strip()
            if bool(evidence_id) == bool(theme):
                return self._json(
                    {"success": False, "message": "forget requires exactly one of evidence_id or theme", "field": "payload"},
                    status=400,
                )
            command_payload = {"evidence_id": evidence_id} if evidence_id else {"theme": theme}
            mapped_action = "forget_evidence" if evidence_id else "forget_theme"
        elif action == "do_not_mention":
            mapped_action = "set_boundary"
            command_payload = {
                "boundary_key": "memory_reference_depth",
                "value": "never",
                "source_summary": "不要主动提起过往经历",
            }
        elif action == "set_initiative_quiet_hours":
            mapped_action = action
            command_payload = {
                "enabled": command_payload.get("enabled"),
                "start": command_payload.get("start"),
                "end": command_payload.get("end"),
            }
        else:
            contract = {
                "too_proactive": ("initiative_level", "low", "减少主动打扰"),
                "too_personal": ("memory_reference_depth", "shallow", "少量联系过往"),
                "disable_initiative": ("initiative_level", "disabled", "不主动发起话题"),
            }[action]
            mapped_action = "set_interaction_contract"
            command_payload = {
                "dimension": contract[0],
                "value": contract[1],
                "scope": "all",
                "safe_label": contract[2],
                "safe_scope": "所有场景",
            }

        companion_mind = getattr(self.runtime, "companion_mind", None)
        if companion_mind is None:
            return self._json(
                {"success": False, "message": "companion memory unavailable"},
                status=503,
            )
        subject_context = self._companion_subject_context(request, subject)
        if isinstance(subject_context, web.Response):
            return subject_context
        command_payload.update(
            {
                "now": datetime.now().astimezone().isoformat(),
                "idempotency_key": idempotency_key,
            }
        )
        try:
            result = companion_mind.apply_control(
                CompanionControlCommand(
                    action=mapped_action,
                    subject=subject_context,
                    payload=command_payload,
                )
            )
        except PermissionError:
            return self._json(
                {"success": False, "message": "confirmed owner required"},
                status=403,
            )
        except CompanionIdempotencyConflict:
            return self._json(
                {"success": False, "message": "idempotency key conflict", "field": "idempotency_key"},
                status=409,
            )
        except ValueError as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)
        except CompanionUnavailableError:
            return self._json(
                {"success": False, "message": "companion memory unavailable"},
                status=503,
            )
        return self._json(
            {
                "success": True,
                "action": action,
                "mapped_action": mapped_action,
                "idempotency_key": idempotency_key,
                "result": asdict(result),
                "message": "陪伴设置已更新",
            }
        )

    async def handle_miniprogram_todo_update(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        try:
            payload = json.loads(await request.text())
            if not isinstance(payload, dict):
                raise ValueError("json object required")
            self._validate_todo_payload(payload, partial=True)
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        except (TypeError, ValueError) as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)

        user = request["xiaoxin_user"]
        todo_id = str(request.match_info.get("todo_id") or "").strip()
        previous = self.runtime.identity_store.get_student_todo(user.id, todo_id)
        todo = self.runtime.identity_store.update_student_todo(
            user.id,
            todo_id,
            payload,
        )
        if todo is None:
            return self._json({"success": False, "message": "todo not found"}, status=404)
        observation_kind = (
            "todo_completed"
            if previous is not None
            and previous.get("status") != "done"
            and todo.get("status") == "done"
            else "todo_updated"
        )
        self._observe_todo_event(user.id, todo, observation_kind)
        await self._refresh_user_overview(user.id, "todo_updated")
        return self._json(
            {"success": True, "todo": self._student_todo_payload(todo)}
        )

    async def handle_miniprogram_todo_delete(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        user = request["xiaoxin_user"]
        todo_id = str(request.match_info.get("todo_id") or "").strip()
        previous = self.runtime.identity_store.get_student_todo(user.id, todo_id)
        deleted = self.runtime.identity_store.delete_student_todo(user.id, todo_id)
        if deleted:
            self._observe_todo_event(
                user.id,
                {**previous, "status": "deleted"},
                "todo_deleted",
                previous_status=str(previous.get("status") or ""),
                occurred_at=datetime.now().astimezone().isoformat(),
            )
            await self._refresh_user_overview(user.id, "todo_deleted")
        return self._json({"success": True, "deleted": deleted})

    async def handle_miniprogram_curriculum_overview(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        user = request["xiaoxin_user"]
        date_text = str(request.query.get("date") or local_date_text())
        try:
            overview = self._curriculum_overview(user.id, date_text)
        except ValueError as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)
        return self._json({"success": True, "overview": overview})

    async def handle_miniprogram_overview(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        user = request["xiaoxin_user"]
        date_text = str(request.query.get("date") or local_date_text())
        try:
            overview = self._student_overview(user.id, date_text)
        except ValueError as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)
        return self._json({"success": True, "overview": overview})

    async def handle_miniprogram_diagnostics(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        if not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        user = request["xiaoxin_user"]
        date_text = str(request.query.get("date") or local_date_text())
        checks = self._miniprogram_diagnostic_checks(request, user, date_text)
        summary = {
            "ok": sum(1 for check in checks if check["status"] == "ok"),
            "warning": sum(1 for check in checks if check["status"] == "warning"),
            "error": sum(1 for check in checks if check["status"] == "error"),
        }
        overall_status = (
            "error"
            if summary["error"]
            else "warning" if summary["warning"] else "ok"
        )
        return self._json(
            {
                "success": True,
                "diagnostics": {
                    "generatedAt": local_datetime().isoformat(timespec="seconds"),
                    "overallStatus": overall_status,
                    "checkedDate": date_text,
                    "summary": summary,
                    "checks": checks,
                },
            }
        )

    async def handle_miniprogram_notification_history(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        owned_device_ids = self._owned_bound_device_ids(request)
        if isinstance(owned_device_ids, web.Response):
            return owned_device_ids
        records = self._miniprogram_notification_records(owned_device_ids)
        notifications = [
            self._miniprogram_notification_history_payload(record)
            for record in records
            if not self._is_companion_initiative_record(record)
        ]
        return self._json({"success": True, "notifications": notifications})

    async def handle_miniprogram_companion_history(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        compliance_denied = self._require_compliance_capability(
            request["xiaoxin_user"].id,
            Capability.COMPANION_INITIATIVE,
        )
        if compliance_denied is not None:
            return compliance_denied

        owned_device_ids = self._owned_bound_device_ids(request)
        if isinstance(owned_device_ids, web.Response):
            return owned_device_ids
        records = self._miniprogram_notification_records(owned_device_ids)
        messages = [
            self._miniprogram_companion_history_payload(record)
            for record in records
            if self._is_companion_initiative_record(record)
        ]
        return self._json({"success": True, "messages": messages})

    async def handle_devices(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        auth = self._auth_service()
        if auth is None or not hasattr(self.runtime, "identity_store"):
            return self._json({"devices": self.runtime.registry.list_devices()})

        user = request["xiaoxin_user"]
        is_admin = getattr(user, "role", "user") == "admin"
        identity_devices = (
            self.runtime.identity_store.list_all_devices()
            if is_admin
            else [
                device
                for device in self.runtime.identity_store.list_devices_for_user(user.id)
                if device.owner_user_id == user.id
            ]
        )
        runtime_by_id = {
            item["device_id"]: item for item in self.runtime.registry.list_devices()
        }
        devices = []
        for device in identity_devices:
            state = runtime_by_id.get(device.device_id, {})
            devices.append(
                {
                    **state,
                    "device_id": device.device_id,
                    "state": state.get("state", "offline"),
                    "doorbell_state": state.get("doorbell_state", "offline"),
                    "owner_user_id": device.owner_user_id,
                    "display_name": device.display_name,
                    "bind_status": device.bind_status,
                    "overview": self._overview_diagnostics(device.device_id),
                }
            )
        known_ids = {device.device_id for device in identity_devices}
        for device_id, state in runtime_by_id.items():
            if not is_admin:
                continue
            if device_id in known_ids:
                continue
            devices.append(
                {
                    **state,
                    "device_id": device_id,
                    "state": state.get("state", "offline"),
                    "doorbell_state": state.get("doorbell_state", "offline"),
                    "owner_user_id": None,
                    "display_name": device_id,
                    "bind_status": "seen",
                    "overview": self._overview_diagnostics(device_id),
                }
            )
        return self._json({"devices": devices})

    async def handle_admin_devices(self, request: web.Request) -> web.Response:
        denied = self._admin_required(request)
        if denied is not None:
            return denied
        return await self.handle_devices(request)

    def _overview_diagnostics(self, device_id: str) -> dict[str, object]:
        store = self._weather_location_store()
        row: dict[str, object] = {}
        if store is not None:
            get_diagnostics = getattr(store, "get_snapshot_diagnostics", None)
            if callable(get_diagnostics):
                row = get_diagnostics(device_id) or {}

        date_text = (
            str(row.get("weather_date") or "")
            if row
            else local_date_text()
        )
        location = None
        if store is not None:
            get_location = getattr(store, "get_location", None)
            if callable(get_location):
                location = get_location(device_id)
        weather = self._overview_weather_diagnostics(
            store,
            location,
            date_text,
        )
        return {
            "revision": row.get("revision"),
            "publish_state": row.get("publish_state"),
            "published_at": row.get("published_at"),
            "last_error": self._safe_overview_error(row.get("last_error")),
            "attempts": row.get("publish_attempts", 0),
            "weather": weather,
        }

    def _overview_weather_diagnostics(
        self,
        store: Any,
        location: dict[str, object] | None,
        date_text: str,
    ) -> dict[str, object]:
        if not date_text:
            return {
                "mode": str(location.get("mode") or "") if location else None,
                "city": str(location.get("city") or "") if location else "",
                "date": "",
                "cache_status": "unknown",
            }
        if location is None:
            return {
                "mode": None,
                "city": "",
                "date": date_text,
                "cache_status": "not_configured",
            }
        mode = str(location.get("mode") or "")
        city = str(location.get("city") or "")
        province = str(location.get("province") or "")
        country_code = str(location.get("country_code") or "CN")
        service = getattr(self.runtime, "overview_service", None)
        provider = str(getattr(service, "weather_provider_name", "open-meteo"))
        cache_status = "missing"
        get_daily_weather = getattr(store, "get_daily_weather", None)
        if callable(get_daily_weather) and get_daily_weather(
            province,
            city,
            date_text,
            provider,
            country_code=country_code,
        ) is not None:
            cache_status = "cached"
        else:
            get_retry_state = getattr(store, "get_weather_retry_state", None)
            retry = (
                get_retry_state(
                    province,
                    city,
                    date_text,
                    provider,
                    country_code=country_code,
                )
                if callable(get_retry_state)
                else None
            )
            if retry is not None:
                cache_status = (
                    "retry_scheduled"
                    if retry.get("next_attempt_at")
                    else "failed"
                )
        return {
            "mode": mode,
            "city": city,
            "date": date_text,
            "cache_status": cache_status,
        }

    @staticmethod
    def _safe_overview_error(value: object) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text in {
            "overview_mqtt_disabled",
            "overview_publish_failed",
            "overview_puback_timeout",
            "overview_payload_invalid",
            "overview_revision_stale",
            "overview_device_mismatch",
            "overview_device_unbound",
            "overview_ip_hmac_unconfigured",
            "weather_cache_missing",
        }:
            return text
        return "overview_sync_failed"

    async def handle_manual_bind_device(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        compliance_denied = self._require_compliance_capability(
            request["xiaoxin_user"].id,
            Capability.DEVICE_BIND,
        )
        if compliance_denied is not None:
            return compliance_denied

        auth = self._auth_service()
        if auth is None or not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        device_id = str(payload.get("device_id") or "").strip()
        display_name = str(payload.get("display_name") or device_id).strip()
        if not device_id:
            return self._json(
                {"success": False, "message": "device_id required", "field": "device_id"},
                status=400,
            )
        try:
            validate_mqtt_topic_segment(device_id, "device_id")
        except ValueError:
            return self._json(
                {"success": False, "message": "invalid device_id"},
                status=400,
            )
        return self._json(
            {"success": False, "message": "activation_required"},
            status=403,
        )

    async def handle_activation_bind_device(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        compliance_denied = self._require_compliance_capability(
            request["xiaoxin_user"].id,
            Capability.DEVICE_BIND,
        )
        if compliance_denied is not None:
            return compliance_denied

        if not hasattr(self.runtime, "identity_store") or not hasattr(
            self.runtime, "activation_store"
        ):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        code = str(payload.get("code") or "").strip()
        display_name = str(payload.get("display_name") or "").strip()
        if not code or not code.isdigit() or len(code) != 6:
            return self._json(
                {"success": False, "message": "code required", "field": "code"},
                status=400,
            )

        session = self.runtime.activation_store.get_activation_by_code(code)
        if session is None or session.consumed_at is not None:
            return self._json(
                {"success": False, "message": "activation code not found"},
                status=404,
            )
        if self.runtime.activation_store.is_expired(session):
            return self._json(
                {"success": False, "message": "activation code expired"},
                status=410,
            )
        try:
            safe_device_id = validate_mqtt_topic_segment(session.device_id, "device_id")
        except ValueError:
            return self._json(
                {"success": False, "message": "invalid device_id"},
                status=400,
            )

        user = request["xiaoxin_user"]
        self.runtime.identity_store.upsert_seen_device(
            safe_device_id,
            display_name or safe_device_id,
        )
        try:
            device = self.runtime.identity_store.bind_device(
                safe_device_id,
                user.id,
                display_name or safe_device_id,
            )
        except ValueError as exc:
            status = 409 if "already bound" in str(exc) else 400
            return self._json({"success": False, "message": str(exc)}, status=status)

        self.runtime.activation_store.mark_activation_consumed(code)
        await self._refresh_user_overview(user.id, "device_bound")
        return self._json({"success": True, "device": self._device_payload(device)})

    async def handle_bind_device(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        compliance_denied = self._require_compliance_capability(
            request["xiaoxin_user"].id,
            Capability.DEVICE_BIND,
        )
        if compliance_denied is not None:
            return compliance_denied

        auth = self._auth_service()
        if auth is None or not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        try:
            validate_mqtt_topic_segment(request.match_info["device_id"], "device_id")
        except ValueError:
            return self._json(
                {"success": False, "message": "invalid device_id"},
                status=400,
            )
        return self._json(
            {"success": False, "message": "activation_required"},
            status=403,
        )

    async def handle_unbind_device(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        if not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        try:
            device_id = validate_mqtt_topic_segment(
                request.match_info["device_id"],
                "device_id",
            )
        except ValueError:
            return self._json(
                {"success": False, "message": "invalid device_id"},
                status=400,
            )

        user = request["xiaoxin_user"]
        device = self.runtime.identity_store.get_device_by_device_id(device_id)
        if (
            device is None
            or device.bind_status != DEVICE_BOUND
            or device.owner_user_id != user.id
        ):
            return self._json(
                {"success": False, "message": "device_not_bound"},
                status=403,
            )

        unbound = self.runtime.identity_store.unbind_device(device_id, user.id)
        if unbound:
            await self._clear_unbound_device_overview(device_id, "device_unbound")
        return self._json({"success": True, "device_id": device_id})

    async def handle_wake_device_by_id(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        raw_device_id = str(payload.get("device_id") or "").strip()
        if not raw_device_id:
            return self._json(
                {"success": False, "message": "device_id required", "field": "device_id"},
                status=400,
            )
        try:
            device_id = validate_mqtt_topic_segment(raw_device_id, "device_id")
        except ValueError:
            return self._json(
                {"success": False, "message": "invalid device_id"},
                status=400,
            )

        tenant = load_tenant_config(self.config)
        doorbell_client = getattr(self.runtime, "doorbell_client", None)
        if doorbell_client is None:
            return self._json(
                {"success": False, "message": "doorbell_client_not_started"},
                status=400,
            )
        diagnostic_state = getattr(doorbell_client, "diagnostic_state", lambda: "ok")()
        if diagnostic_state != "ok":
            return self._json(
                {"success": False, "message": diagnostic_state},
                status=400,
            )
        if not doorbell_client.publish_wake(device_id, tenant.tenant_id):
            return self._json(
                {"success": False, "message": "wake_publish_failed"},
                status=400,
            )
        return self._json({"success": True, "device_id": device_id})

    async def handle_wake_device(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        try:
            device_id = validate_mqtt_topic_segment(
                request.match_info["device_id"],
                "device_id",
            )
        except ValueError:
            return self._json(
                {"success": False, "message": "invalid device_id"},
                status=400,
            )
        tenant = load_tenant_config(self.config)
        device_denied = self._deny_for_wake_target(request, device_id, tenant.tenant_id)
        if device_denied is not None:
            return device_denied

        doorbell_client = getattr(self.runtime, "doorbell_client", None)
        if doorbell_client is None:
            return self._json(
                {"success": False, "message": "doorbell_client_not_started"},
                status=400,
            )
        diagnostic_state = getattr(doorbell_client, "diagnostic_state", lambda: "ok")()
        if diagnostic_state != "ok":
            return self._json(
                {"success": False, "message": diagnostic_state},
                status=400,
            )
        if not doorbell_client.publish_wake(device_id, tenant.tenant_id):
            return self._json(
                {"success": False, "message": "wake_publish_failed"},
                status=400,
            )
        return self._json({"success": True, "device_id": device_id})

    async def handle_device_text_chat(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        device_id = str(request.match_info.get("device_id") or "").strip()
        device_denied = self._deny_for_control_command_target(request, device_id)
        if device_denied is not None:
            return device_denied

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        if not isinstance(payload, dict):
            return self._json(
                {"success": False, "message": "invalid json object", "field": "body"},
                status=400,
            )

        text = payload.get("text")
        if text is not None and not isinstance(text, str):
            return self._json(
                {"success": False, "message": "text must be string", "field": "text"},
                status=400,
            )
        text = str(text or "").strip()
        if not text:
            return self._json(
                {"success": False, "message": "text required", "field": "text"},
                status=400,
            )
        if len(text) > 500:
            return self._json(
                {"success": False, "message": "text too long", "field": "text"},
                status=400,
            )

        capability = (
            Capability.TOOL_QUERY
            if is_existing_tool_turn(text)
            else Capability.COMPANION_CHAT
        )
        compliance_denied = self._require_device_owner_capability(
            device_id,
            capability,
        )
        if compliance_denied is not None:
            return compliance_denied

        evaluation_fields_present = any(
            key in payload
            for key in ("evaluation_run_id", "case_id", "await_tts_terminal")
        )
        evaluation_run_id = payload.get("evaluation_run_id")
        case_id = payload.get("case_id")
        await_tts_terminal = payload.get("await_tts_terminal", False)
        if evaluation_fields_present:
            user = request["xiaoxin_user"]
            evaluation_enabled = (
                os.getenv("XIAOXIN_EVALUATION_MODE") == "1"
                or self.config.get("xiaoxin_evaluation_mode") is True
            )
            if getattr(user, "role", "user") != "admin" or not evaluation_enabled:
                return self._json(
                    {"success": False, "message": "evaluation mode not allowed"},
                    status=403,
                )
            for field_name, value in (
                ("evaluation_run_id", evaluation_run_id),
                ("case_id", case_id),
            ):
                if (
                    not isinstance(value, str)
                    or _EVALUATION_ID_PATTERN.fullmatch(value) is None
                ):
                    return self._json(
                        {
                            "success": False,
                            "message": f"{field_name} is invalid",
                            "field": field_name,
                        },
                        status=400,
                    )
            if not isinstance(await_tts_terminal, bool):
                return self._json(
                    {
                        "success": False,
                        "message": "await_tts_terminal must be boolean",
                        "field": "await_tts_terminal",
                    },
                    status=400,
                )

        simulated_as_of = None
        simulated_as_of_text = payload.get("simulated_as_of")
        if simulated_as_of_text is not None:
            user = request["xiaoxin_user"]
            if (
                getattr(user, "role", "user") != "admin"
                or os.getenv("XIAOXIN_ALLOW_SIMULATED_TIME") != "1"
            ):
                return self._json(
                    {"success": False, "message": "simulated time not allowed"},
                    status=403,
                )
            if not isinstance(simulated_as_of_text, str):
                return self._json(
                    {
                        "success": False,
                        "message": "simulated_as_of must be string",
                        "field": "simulated_as_of",
                    },
                    status=400,
                )
            try:
                simulated_as_of = datetime.fromisoformat(simulated_as_of_text)
            except ValueError:
                simulated_as_of = None
            if (
                simulated_as_of is None
                or simulated_as_of.tzinfo is None
                or simulated_as_of.utcoffset() is None
            ):
                return self._json(
                    {
                        "success": False,
                        "message": "simulated_as_of must be timezone-aware ISO datetime",
                        "field": "simulated_as_of",
                    },
                    status=400,
                )

        speaker_profile_id = payload.get("speaker_profile_id")
        if speaker_profile_id is not None and not isinstance(speaker_profile_id, str):
            return self._json(
                {
                    "success": False,
                    "message": "speaker_profile_id must be string",
                    "field": "speaker_profile_id",
                },
                status=400,
            )
        speaker_profile_id = str(speaker_profile_id or "").strip()
        speaker_override = None
        if speaker_profile_id:
            identity_store = getattr(self.runtime, "identity_store", None)
            profile = (
                identity_store.get_speaker_profile(speaker_profile_id)
                if identity_store is not None
                else None
            )
            device = (
                identity_store.get_device_by_device_id(device_id)
                if identity_store is not None
                else None
            )
            user = request["xiaoxin_user"]
            is_admin = getattr(user, "role", "user") == "admin"
            if (
                profile is None
                or profile.status != SPEAKER_CONFIRMED
                or profile.device_id != device_id
                or device is None
                or profile.owner_user_id != device.owner_user_id
                or (not is_admin and profile.owner_user_id != user.id)
            ):
                return self._json(
                    {
                        "success": False,
                        "message": "speaker profile not allowed",
                        "field": "speaker_profile_id",
                    },
                    status=403,
                )
            speaker_override = f"voiceprint:{profile.speaker_key}"

        conn = self.runtime.registry.get_connection(device_id)
        if conn is None:
            return self._json(
                {"success": False, "message": "device not connected"},
                status=409,
            )

        accelerated_work = None
        try:
            chat_result = await conn.submit_control_text_chat(
                text,
                speaker=speaker_override,
                simulated_as_of=simulated_as_of,
                await_tts_terminal=bool(await_tts_terminal),
                evaluation_run_id=evaluation_run_id,
                evaluation_case_id=case_id,
            )
            if simulated_as_of is not None:
                companion_mind = getattr(self.runtime, "companion_mind", None)
                if companion_mind is None:
                    raise RuntimeError("companion worker is not available")
                subject_context = getattr(conn, "companion_subject_context", None)
                pet_id = getattr(subject_context, "pet_id", None)
                if not isinstance(pet_id, str) or not pet_id:
                    raise RuntimeError("companion pet is not available")
                work_result = await companion_mind.run_due_memory_work(
                    now=simulated_as_of.isoformat(),
                    pet_id=pet_id,
                    limit=20,
                )
                accelerated_work = {
                    "claimed": work_result.claimed,
                    "succeeded": work_result.succeeded,
                    "retried": work_result.retried,
                    "failed": work_result.failed,
                }
        except ValueError as exc:
            return self._json(
                {"success": False, "message": str(exc), "field": "text"},
                status=400,
            )
        except RuntimeError as exc:
            if str(exc) == "text chat busy":
                return self._json(
                    {"success": False, "message": "text chat busy"},
                    status=409,
                )
            self.logger.bind(tag=__name__).exception(
                "text chat failed device_id=%s",
                device_id,
            )
            return self._json(
                {"success": False, "message": "text chat failed"},
                status=500,
            )
        except Exception:
            self.logger.bind(tag=__name__).exception(
                "text chat failed device_id=%s",
                device_id,
            )
            return self._json(
                {"success": False, "message": "text chat failed"},
                status=500,
            )

        response = {"success": True, "message": "submitted"}
        if evaluation_fields_present:
            response.update(
                {
                    "evaluation_run_id": evaluation_run_id,
                    "case_id": case_id,
                    "event_id": chat_result.event_id,
                    "sentence_id": chat_result.sentence_id,
                    "submitted_at": chat_result.submitted_at,
                    "assistant_text": chat_result.assistant_text,
                    "tts_outcome": chat_result.tts_outcome,
                    "tts_reason": chat_result.tts_reason,
                }
            )
        if simulated_as_of is not None:
            response["simulated_as_of"] = simulated_as_of.isoformat()
            response["accelerated_work"] = accelerated_work
        return self._json(response)

    async def handle_sync_device_overview(self, request: web.Request) -> web.Response:
        self.logger.bind(tag="xiaoxin.overview_sync").info("overview sync request received")
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            self.logger.bind(tag="xiaoxin.overview_sync").info("overview sync denied: unauthorized")
            return denied

        device_id = str(request.match_info.get("device_id") or "").strip()
        self.logger.bind(tag="xiaoxin.overview_sync").info(
            f"overview sync target device_id={device_id or '<empty>'}"
        )
        device_denied = self._deny_for_control_command_target(request, device_id)
        if device_denied is not None:
            self.logger.bind(tag="xiaoxin.overview_sync").info(
                f"overview sync denied: unknown device_id={device_id}"
            )
            return device_denied

        if not hasattr(self.runtime, "registry"):
            self.logger.bind(tag="xiaoxin.overview_sync").info(
                f"overview sync failed: registry unavailable device_id={device_id}"
            )
            return self._json(
                {"success": False, "message": "device registry unavailable"},
                status=404,
            )

        conn = self.runtime.registry.get_connection(device_id)
        if conn is None:
            self.logger.bind(tag="xiaoxin.overview_sync").info(
                f"overview sync failed: no live connection device_id={device_id}"
            )
            return self._json(
                {"success": False, "message": "device is not connected"},
                status=409,
            )

        user = request["xiaoxin_user"]
        date_text = str(request.query.get("date") or local_date_text())
        try:
            overview = self._student_overview(user.id, date_text, device_id=device_id)
        except ValueError as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)

        payload = self._overview_update_payload(device_id, overview)
        try:
            self.logger.bind(tag="xiaoxin.overview_sync").info(
                "sending overview "
                f"device_id={device_id} "
                f"weather={overview.get('weather', {}).get('summary')!r} "
                f"course={overview.get('course', {}).get('title')!r} "
                f"todo={overview.get('todo', {}).get('detail')!r}"
            )
            await conn.send_xiaoxin_event(payload)
        except Exception:
            self.logger.bind(tag="xiaoxin.overview_sync").exception(
                f"overview sync send failed device_id={device_id}"
            )
            return self._json(
                {"success": False, "message": "overview sync failed"},
                status=502,
            )

        self.logger.bind(tag="xiaoxin.overview_sync").info(
            f"overview sync send ok device_id={device_id}"
        )
        return self._json(
            {
                "success": True,
                "device_id": device_id,
                "overview": overview,
                "payload": payload,
            }
        )

    async def handle_sync_device_overview_mqtt(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        device_id = str(request.match_info.get("device_id") or "").strip()
        device_denied = self._deny_for_control_command_target(request, device_id)
        if device_denied is not None:
            return device_denied

        service = getattr(self.runtime, "overview_service", None)
        refresh_device = getattr(service, "refresh_device", None)
        if not callable(refresh_device):
            return self._json(
                {"success": False, "message": "overview mqtt sync unavailable"},
                status=503,
            )
        try:
            result = await refresh_device(device_id, "manual_resync")
        except Exception:
            self.logger.bind(tag="xiaoxin.overview").warning(
                "manual overview mqtt sync failed device_id=%s",
                device_id,
            )
            return self._json(
                {"success": False, "message": "overview mqtt sync failed"},
                status=502,
            )
        if result.get("error_code") == "overview_mqtt_disabled":
            return self._json(
                {"success": False, "message": "overview_mqtt_disabled"},
                status=503,
            )
        return self._json(
            {
                "success": True,
                "device_id": device_id,
                "revision": result.get("revision"),
                "publish_state": result.get("publish_state"),
            }
        )

    def _device_payload(self, device: Any) -> dict[str, Any]:
        return {
            "device_id": device.device_id,
            "owner_user_id": device.owner_user_id,
            "display_name": device.display_name,
            "bind_status": device.bind_status,
            "bound_at": getattr(device, "bound_at", None) or "",
        }

    async def _refresh_user_overview(self, user_id: str, reason: str) -> None:
        service = getattr(self.runtime, "overview_service", None)
        if service is None:
            return
        try:
            await service.refresh_user_devices(user_id, reason)
        except Exception:
            self.logger.bind(tag="xiaoxin.overview").exception(
                f"overview refresh failed reason={reason}"
            )

    async def _clear_unbound_device_overview(
        self, device_id: str, reason: str
    ) -> None:
        service = getattr(self.runtime, "overview_service", None)
        if service is None:
            return
        try:
            await service.clear_unbound_device(device_id, reason)
        except Exception:
            self.logger.bind(tag="xiaoxin.overview").exception(
                f"overview clear failed reason={reason}"
            )

    async def handle_speakers(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        auth = self._auth_service()
        if auth is None or not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        user = request["xiaoxin_user"]
        speakers = self.runtime.identity_store.list_speakers_for_user(user.id)
        return self._json({"speakers": [speaker.__dict__ for speaker in speakers]})

    async def handle_admin_speakers(self, request: web.Request) -> web.Response:
        denied = self._admin_required(request)
        if denied is not None:
            return denied
        speakers = self.runtime.identity_store.list_all_speakers()
        return self._json(
            {
                "speakers": [
                    {
                        "id": speaker.id,
                        "owner_user_id": speaker.owner_user_id,
                        "device_id": speaker.device_id,
                        "display_name": speaker.display_name,
                        "status": speaker.status,
                        "created_at": speaker.created_at,
                        "last_seen_at": speaker.last_seen_at,
                    }
                    for speaker in speakers
                ]
            }
        )

    async def handle_update_speaker(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        auth = self._auth_service()
        if auth is None or not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        display_name = str(payload.get("display_name") or "")
        if not display_name.strip():
            return self._json(
                {"success": False, "message": "display_name required", "field": "display_name"},
                status=400,
            )

        speaker_id = request.match_info["speaker_id"]
        user = request["xiaoxin_user"]
        updated = self.runtime.identity_store.update_speaker_display_name(
            speaker_id,
            user.id,
            display_name,
        )
        if not updated:
            return self._json({"success": False, "message": "speaker not found"}, status=404)
        return self._json({"success": True})

    async def handle_archive_speaker(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        auth = self._auth_service()
        if auth is None or not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        speaker_id = request.match_info["speaker_id"]
        user = request["xiaoxin_user"]
        archived = self.runtime.identity_store.archive_speaker(speaker_id, user.id)
        if not archived:
            return self._json({"success": False, "message": "speaker not found"}, status=404)
        return self._json({"success": True})

    async def handle_memory_subjects(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        auth = self._auth_service()
        if auth is None or not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        user = request["xiaoxin_user"]
        subjects = self.runtime.identity_store.list_memory_subjects_for_user(user.id)
        return self._json({"memory_subjects": [subject.__dict__ for subject in subjects]})

    async def handle_admin_memory_subjects(self, request: web.Request) -> web.Response:
        denied = self._admin_required(request)
        if denied is not None:
            return denied
        try:
            page = int(request.query.get("page", "1"))
            page_size = int(request.query.get("page_size", "50"))
        except ValueError:
            return self._json(
                {"success": False, "code": "invalid_pagination", "message": "invalid pagination"},
                status=400,
            )
        service = self._admin_memory_service()
        result = service.list_subjects(
            search=str(request.query.get("search") or ""),
            owner_user_id=str(request.query.get("owner_user_id") or ""),
            device_id=str(request.query.get("device_id") or ""),
            subject_kind=str(request.query.get("kind") or ""),
            readiness_code=str(request.query.get("readiness") or ""),
            include_merged=str(request.query.get("include_merged") or "").lower()
            in {"1", "true", "yes"},
            page=page,
            page_size=page_size,
        )
        return self._json({"success": True, **result})

    async def handle_admin_memory_subject_detail(
        self, request: web.Request
    ) -> web.Response:
        denied = self._admin_required(request)
        if denied is not None:
            return denied
        actor = request["xiaoxin_user"]
        subject_id = str(request.match_info.get("subject_id") or "").strip()
        service = self._admin_memory_service()
        identity = service.get_subject(subject_id)
        if identity is None:
            self.runtime.identity_store.record_admin_audit(
                actor_user_id=actor.id,
                action="memory_detail_read",
                result_status="failure",
                failure_code="subject_not_found",
                reason_code="admin_console_diagnostics",
            )
            return self._json(
                {"success": False, "code": "subject_not_found", "message": "subject not found"},
                status=404,
            )
        companion_mind = getattr(self.runtime, "companion_mind", None)
        if companion_mind is None and identity["readiness"]["code"] == "ready":
            audit = self.runtime.identity_store.record_admin_audit(
                actor_user_id=actor.id,
                target_owner_user_id=identity["owner"]["id"],
                target_subject_id=subject_id,
                action="memory_detail_read",
                result_status="failure",
                failure_code="projection_unavailable",
                reason_code="admin_console_diagnostics",
            )
            return self._json(
                {
                    "success": False,
                    "code": "projection_unavailable",
                    "message": "companion memory unavailable",
                    "audit_id": audit["id"],
                },
                status=503,
            )
        try:
            projection = service.project_subject(identity, companion_mind)
        except Exception as exc:
            self.logger.bind(tag="xiaoxin.admin_memory").error(
                "admin memory projection failed: {}", type(exc).__name__
            )
            audit = self.runtime.identity_store.record_admin_audit(
                actor_user_id=actor.id,
                target_owner_user_id=identity["owner"]["id"],
                target_subject_id=subject_id,
                action="memory_detail_read",
                result_status="failure",
                failure_code="projection_unavailable",
                reason_code="admin_console_diagnostics",
            )
            return self._json(
                {
                    "success": False,
                    "code": "projection_unavailable",
                    "message": "companion memory unavailable",
                    "audit_id": audit["id"],
                },
                status=503,
            )
        audit = self.runtime.identity_store.record_admin_audit(
            actor_user_id=actor.id,
            target_owner_user_id=identity["owner"]["id"],
            target_subject_id=subject_id,
            action="memory_detail_read",
            result_status="success",
            reason_code="admin_console_diagnostics",
        )
        diagnostics = projection.get("payload", {}).get("diagnostics", {})
        return self._json(
            {
                "success": True,
                "identity": identity,
                "readiness": identity["readiness"],
                "projection": projection,
                "diagnostics": diagnostics,
                "audit": {"id": audit["id"], "created_at": audit["created_at"]},
            }
        )

    async def handle_admin_memory_control(self, request: web.Request) -> web.Response:
        denied = self._admin_write_required(request)
        if denied is not None:
            return denied
        actor = request["xiaoxin_user"]
        subject_id = str(request.match_info.get("subject_id") or "").strip()
        service = self._admin_memory_service()
        identity = service.get_subject(subject_id)
        if identity is None:
            self.runtime.identity_store.record_admin_audit(
                actor_user_id=actor.id,
                action="memory_control:unknown",
                result_status="failure",
                failure_code="subject_not_found",
                reason_code="admin_console_control",
            )
            return self._json(
                {"success": False, "code": "subject_not_found", "message": "subject not found"},
                status=404,
            )
        try:
            body = json.loads(await request.text())
        except json.JSONDecodeError:
            self._record_admin_control_failure(
                actor.id, identity, "unknown", "invalid_json", None
            )
            return self._json(
                {"success": False, "code": "invalid_json", "message": "invalid json"},
                status=400,
            )
        if not isinstance(body, dict):
            self._record_admin_control_failure(
                actor.id, identity, "unknown", "invalid_body", None
            )
            return self._json(
                {"success": False, "code": "invalid_body", "message": "json object required"},
                status=400,
            )
        action = str(body.get("action") or "").strip()
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        allowed_actions = {
            "reset_relationship",
            "forget_evidence",
            "forget_theme",
            "correct_evidence",
            "set_boundary",
            "revoke_boundary",
            "purge_personal_memory",
            "revoke_adjustment",
            "set_interaction_contract",
            "revoke_interaction_contract",
            "restore_default_expression",
            "set_growth_moments_enabled",
            "set_initiative_quiet_hours",
            "confirm_candidate",
            "reject_candidate",
        }
        if action not in allowed_actions:
            self._record_admin_control_failure(
                actor.id, identity, action or "unknown", "unsupported_control_action", idempotency_key or None
            )
            return self._json(
                {
                    "success": False,
                    "code": "unsupported_control_action",
                    "message": "unsupported control action",
                },
                status=400,
            )
        if not idempotency_key:
            self._record_admin_control_failure(
                actor.id, identity, action, "idempotency_key_required", None
            )
            return self._json(
                {
                    "success": False,
                    "code": "idempotency_key_required",
                    "message": "idempotency_key required",
                },
                status=400,
            )
        required_confirmation = {
            "reset_relationship": "RESET_RELATIONSHIP",
            "purge_personal_memory": "PURGE_PERSONAL_MEMORY",
        }.get(action)
        if required_confirmation and body.get("confirmation") != required_confirmation:
            self._record_admin_control_failure(
                actor.id,
                identity,
                action,
                "confirmation_required",
                idempotency_key,
            )
            return self._json(
                {
                    "success": False,
                    "code": "confirmation_required",
                    "message": f"confirmation must equal {required_confirmation}",
                },
                status=400,
            )
        if identity["readiness"]["code"] != "ready":
            self._record_admin_control_failure(
                actor.id,
                identity,
                action,
                str(identity["readiness"]["code"]),
                idempotency_key,
            )
            return self._json(
                {
                    "success": False,
                    "code": identity["readiness"]["code"],
                    "message": "memory subject is not writable",
                },
                status=409,
            )
        payload = body.get("payload", {})
        if not isinstance(payload, dict):
            self._record_admin_control_failure(
                actor.id, identity, action, "invalid_payload", idempotency_key
            )
            return self._json(
                {"success": False, "code": "invalid_payload", "message": "payload must be an object"},
                status=400,
            )
        companion_mind = getattr(self.runtime, "companion_mind", None)
        if companion_mind is None:
            self._record_admin_control_failure(
                actor.id, identity, action, "companion_unavailable", idempotency_key
            )
            return self._json(
                {
                    "success": False,
                    "code": "companion_unavailable",
                    "message": "companion memory unavailable",
                },
                status=503,
            )
        command_payload = {
            **payload,
            "now": datetime.now().astimezone().isoformat(),
            "idempotency_key": idempotency_key,
        }
        try:
            result = companion_mind.apply_control(
                CompanionControlCommand(
                    action=action,
                    subject=service.subject_context(identity),
                    payload=command_payload,
                )
            )
        except PermissionError:
            self._record_admin_control_failure(
                actor.id, identity, action, "confirmed_owner_required", idempotency_key
            )
            return self._json(
                {
                    "success": False,
                    "code": "confirmed_owner_required",
                    "message": "confirmed owner required",
                },
                status=403,
            )
        except CompanionIdempotencyConflict:
            self._record_admin_control_failure(
                actor.id, identity, action, "idempotency_conflict", idempotency_key
            )
            return self._json(
                {
                    "success": False,
                    "code": "idempotency_conflict",
                    "message": "idempotency key reused for a different command",
                },
                status=409,
            )
        except ValueError:
            self._record_admin_control_failure(
                actor.id, identity, action, "invalid_control", idempotency_key
            )
            return self._json(
                {
                    "success": False,
                    "code": "invalid_control",
                    "message": "control payload is invalid",
                },
                status=400,
            )
        except CompanionUnavailableError:
            self._record_admin_control_failure(
                actor.id, identity, action, "companion_unavailable", idempotency_key
            )
            return self._json(
                {
                    "success": False,
                    "code": "companion_unavailable",
                    "message": "companion memory unavailable",
                },
                status=503,
            )
        audit = self.runtime.identity_store.record_admin_audit(
            actor_user_id=actor.id,
            target_owner_user_id=identity["owner"]["id"],
            target_subject_id=subject_id,
            action=f"memory_control:{action}",
            result_status="success",
            idempotency_key=idempotency_key,
            reason_code="admin_console_control",
        )
        response = asdict(result)
        response.update(
            {
                "success": True,
                "message": self._companion_control_message(action),
                "audit_id": audit["id"],
            }
        )
        return self._json(response)

    async def handle_admin_merge_memory_subject(
        self, request: web.Request
    ) -> web.Response:
        denied = self._admin_write_required(request)
        if denied is not None:
            return denied
        actor = request["xiaoxin_user"]
        source_id = str(request.match_info.get("subject_id") or "").strip()
        source = self.runtime.identity_store.get_memory_subject(source_id)
        if source is None:
            self._record_admin_merge_audit(
                actor_user_id=actor.id,
                source=None,
                result_status="failure",
                failure_code="subject_not_found",
            )
            return self._json(
                {"success": False, "code": "subject_not_found", "message": "subject not found"},
                status=404,
            )
        try:
            body = json.loads(await request.text())
        except json.JSONDecodeError:
            self._record_admin_merge_audit(
                actor_user_id=actor.id,
                source=source,
                result_status="failure",
                failure_code="invalid_json",
            )
            return self._json(
                {"success": False, "code": "invalid_json", "message": "invalid json"},
                status=400,
            )
        if not isinstance(body, dict):
            self._record_admin_merge_audit(
                actor_user_id=actor.id,
                source=source,
                result_status="failure",
                failure_code="invalid_body",
            )
            return self._json(
                {"success": False, "code": "invalid_body", "message": "json object required"},
                status=400,
            )
        target_id = str(body.get("to_subject_id") or "").strip()
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        if not target_id or not idempotency_key:
            self._record_admin_merge_audit(
                actor_user_id=actor.id,
                source=source,
                result_status="failure",
                failure_code="merge_fields_required",
                idempotency_key=idempotency_key or None,
            )
            return self._json(
                {
                    "success": False,
                    "code": "merge_fields_required",
                    "message": "to_subject_id and idempotency_key required",
                },
                status=400,
            )
        target = self.runtime.identity_store.get_memory_subject(target_id)
        merge_reason = f"merge_source:{source.id}:target:{target_id}"
        if target is None:
            self._record_admin_merge_audit(
                actor_user_id=actor.id,
                source=source,
                result_status="failure",
                failure_code="subject_not_found",
                idempotency_key=idempotency_key,
                reason_code=merge_reason,
            )
            return self._json(
                {"success": False, "code": "subject_not_found", "message": "subject not found"},
                status=404,
            )
        if (
            source.owner_user_id is None
            or target.owner_user_id is None
            or source.owner_user_id != target.owner_user_id
        ):
            self._record_admin_merge_audit(
                actor_user_id=actor.id,
                source=source,
                result_status="failure",
                failure_code="cross_owner_merge_forbidden",
                idempotency_key=idempotency_key,
                reason_code=merge_reason,
            )
            return self._json(
                {
                    "success": False,
                    "code": "cross_owner_merge_forbidden",
                    "message": "cross-owner merge is forbidden",
                },
                status=403,
            )
        if source.kind != target.kind:
            self._record_admin_merge_audit(
                actor_user_id=actor.id,
                source=source,
                result_status="failure",
                failure_code="merge_invalid",
                idempotency_key=idempotency_key,
                reason_code=merge_reason,
            )
            return self._json(
                {"success": False, "code": "merge_invalid", "message": "subjects cannot be merged"},
                status=400,
            )
        prior = self.runtime.identity_store.get_admin_audit_by_idempotency(
            actor_user_id=actor.id,
            action="memory_subject_merge",
            idempotency_key=idempotency_key,
        )
        if prior is not None and prior["reason_code"] != merge_reason:
            audit = self._record_admin_merge_audit(
                actor_user_id=actor.id,
                source=source,
                result_status="failure",
                failure_code="idempotency_conflict",
                idempotency_key=idempotency_key,
                reason_code=merge_reason,
            )
            return self._json(
                {
                    "success": False,
                    "code": "idempotency_conflict",
                    "message": "idempotency key reused for a different merge",
                    "audit_id": audit["id"],
                },
                status=409,
            )
        if prior is not None and prior["result_status"] == "success":
            audit = self._record_admin_merge_audit(
                actor_user_id=actor.id,
                source=source,
                result_status="success",
                idempotency_key=idempotency_key,
                reason_code=merge_reason,
            )
            return self._json(
                {
                    "success": True,
                    "status": "already_applied",
                    "audit_id": audit["id"],
                }
            )
        if source.merged_into_subject_id is not None:
            if source.merged_into_subject_id == target.id:
                status = "already_applied"
            else:
                audit = self._record_admin_merge_audit(
                    actor_user_id=actor.id,
                    source=source,
                    result_status="failure",
                    failure_code="subject_merged",
                    idempotency_key=idempotency_key,
                    reason_code=merge_reason,
                )
                return self._json(
                    {
                        "success": False,
                        "code": "subject_merged",
                        "message": "source subject is already merged",
                        "audit_id": audit["id"],
                    },
                    status=409,
                )
        else:
            try:
                self.runtime.identity_store.create_subject_alias(
                    source.id,
                    target.id,
                    "admin_control_merge",
                )
            except ValueError:
                self._record_admin_merge_audit(
                    actor_user_id=actor.id,
                    source=source,
                    result_status="failure",
                    failure_code="merge_invalid",
                    idempotency_key=idempotency_key,
                    reason_code=merge_reason,
                )
                return self._json(
                    {"success": False, "code": "merge_invalid", "message": "subjects cannot be merged"},
                    status=400,
                )
            status = "applied"
            ingress = getattr(self.runtime, "observation_ingress", None)
            if ingress is not None:
                try:
                    ingress.flush_pending_for_user(source.owner_user_id)
                except Exception:
                    self.logger.bind(tag="xiaoxin.companion_observation").error(
                        "pending observation backfill failed after admin subject merge"
                    )
        audit = self._record_admin_merge_audit(
            actor_user_id=actor.id,
            source=source,
            result_status="success",
            idempotency_key=idempotency_key,
            reason_code=merge_reason,
        )
        return self._json(
            {"success": True, "status": status, "audit_id": audit["id"]}
        )

    def _record_admin_merge_audit(
        self,
        *,
        actor_user_id: str,
        source: Any | None,
        result_status: str,
        failure_code: str | None = None,
        idempotency_key: str | None = None,
        reason_code: str = "admin_console_merge",
    ) -> dict[str, str | None]:
        return self.runtime.identity_store.record_admin_audit(
            actor_user_id=actor_user_id,
            target_owner_user_id=getattr(source, "owner_user_id", None),
            target_subject_id=getattr(source, "id", None),
            action="memory_subject_merge",
            result_status=result_status,
            failure_code=failure_code,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
        )

    def _record_admin_control_failure(
        self,
        actor_user_id: str,
        identity: Mapping[str, Any],
        action: str,
        failure_code: str,
        idempotency_key: str | None,
    ) -> None:
        self.runtime.identity_store.record_admin_audit(
            actor_user_id=actor_user_id,
            target_owner_user_id=identity["owner"]["id"],
            target_subject_id=identity["id"],
            action=f"memory_control:{action}",
            result_status="failure",
            failure_code=failure_code,
            idempotency_key=idempotency_key,
            reason_code="admin_console_control",
        )

    async def handle_admin_audits(self, request: web.Request) -> web.Response:
        denied = self._admin_required(request)
        if denied is not None:
            return denied
        try:
            limit = int(request.query.get("limit", "100"))
        except ValueError:
            return self._json(
                {"success": False, "code": "invalid_limit", "message": "invalid limit"},
                status=400,
            )
        audits = self.runtime.identity_store.list_admin_audits(
            actor_user_id=str(request.query.get("actor_user_id") or "") or None,
            target_subject_id=str(request.query.get("subject_id") or "") or None,
            limit=limit,
        )
        return self._json({"success": True, "audits": audits})

    async def handle_demo_data(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        return self._json(self.demo_data_store.load())

    async def handle_save_demo_data(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        saved = self.demo_data_store.save(payload)
        return self._json({"success": True, **saved})

    async def handle_send_demo_overview(self, request: web.Request) -> web.Response:
        self.logger.bind(tag="xiaoxin.overview_sync").info("demo overview request received")
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            self.logger.bind(tag="xiaoxin.overview_sync").info("demo overview denied: unauthorized")
            return denied

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            self.logger.bind(tag="xiaoxin.overview_sync").info("demo overview denied: invalid json")
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        device_id = str(payload.get("device_id") or "").strip()
        self.logger.bind(tag="xiaoxin.overview_sync").info(
            f"demo overview target device_id={device_id or '<empty>'}"
        )
        if not device_id:
            return self._json(
                {"success": False, "message": "device_id required", "field": "device_id"},
                status=400,
            )

        device_denied = self._deny_for_control_command_target(request, device_id)
        if device_denied is not None:
            self.logger.bind(tag="xiaoxin.overview_sync").info(
                f"demo overview denied: unknown device_id={device_id}"
            )
            return device_denied

        if not hasattr(self.runtime, "registry"):
            self.logger.bind(tag="xiaoxin.overview_sync").info(
                f"demo overview failed: registry unavailable device_id={device_id}"
            )
            return self._json(
                {"success": False, "message": "device registry unavailable"},
                status=404,
            )

        conn = self.runtime.registry.get_connection(device_id)
        if conn is None:
            self.logger.bind(tag="xiaoxin.overview_sync").info(
                f"demo overview failed: no live connection device_id={device_id}"
            )
            return self._json(
                {"success": False, "message": "device is not connected"},
                status=409,
            )

        demo_data = self.demo_data_store.load()
        overview = self._demo_overview(device_id)
        notifications = demo_data["notifications"]
        update_payload = self._overview_update_payload(
            device_id,
            overview,
            notifications=notifications,
        )
        try:
            self.logger.bind(tag="xiaoxin.overview_sync").info(
                "sending demo overview "
                f"device_id={device_id} "
                f"weather={overview.get('weather', {}).get('summary')!r} "
                f"course={overview.get('course', {}).get('title')!r} "
                f"todo={overview.get('todo', {}).get('detail')!r} "
                f"notifications={len(notifications)}"
            )
            await conn.send_xiaoxin_event(update_payload)
        except Exception:
            self.logger.bind(tag="xiaoxin.overview_sync").exception(
                f"demo overview send failed device_id={device_id}"
            )
            return self._json(
                {"success": False, "message": "demo overview sync failed"},
                status=502,
            )

        self.logger.bind(tag="xiaoxin.overview_sync").info(
            f"demo overview send ok device_id={device_id}"
        )
        return self._json(
            {
                "success": True,
                "device_id": device_id,
                "overview": overview,
                "payload": update_payload,
            }
        )

    async def handle_send_demo_notification(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        notification_id = request.match_info["notification_id"]
        notification = self.demo_data_store.notification(notification_id)
        if notification is None:
            return self._json(
                {"success": False, "message": "notification not found"},
                status=404,
            )

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        event_payload = {
            **notification,
            "device_id": str(payload.get("device_id") or ""),
        }
        try:
            event_request = parse_control_event_request(event_payload)
            device_denied = self._deny_for_control_command_target(
                request, event_request.device_id
            )
            if device_denied is not None:
                return device_denied
        except ControlValidationError as exc:
            return self._json(
                {"success": False, "message": str(exc), "field": exc.field},
                status=400,
            )

        try:
            record = await self.runtime.dispatcher.submit(event_request)
        except DispatcherStoppedError:
            return self._json(
                {"success": False, "message": "notification dispatcher is stopped"},
                status=503,
            )
        return self._json(
            {"delivery_id": record.delivery_id, "state": record.state.value}
        )

    async def handle_memory_subject_detail(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        subject = self._owned_memory_subject(request)
        if isinstance(subject, web.Response):
            return subject

        companion_mind = getattr(self.runtime, "companion_mind", None)
        if companion_mind is None:
            return self._json(
                {"success": False, "message": "companion memory unavailable"},
                status=503,
            )
        subject_context = self._companion_subject_context(request, subject)
        if isinstance(subject_context, web.Response):
            return subject_context
        surface = str(request.query.get("surface") or "operator").strip()
        if surface not in {"operator", "miniprogram"}:
            return self._json(
                {"success": False, "message": "unsupported projection surface", "field": "surface"},
                status=400,
            )
        try:
            projection = companion_mind.project(
                CompanionProjectionRequest(
                    subject=subject_context,
                    surface=surface,
                    now=datetime.now().astimezone().isoformat(),
                )
            )
        except CompanionUnavailableError:
            return self._json(
                {"success": False, "message": "companion memory unavailable"},
                status=503,
            )
        response_body = asdict(projection)
        if surface == "miniprogram":
            summary = projection.payload.get("companion_summary")
            if isinstance(summary, Mapping):
                response_body["relationship_stage"] = summary.get(
                    "relationship", "正在相处"
                )
        return self._json(response_body)

    async def handle_companion_memory_control(
        self, request: web.Request
    ) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        subject = self._owned_memory_subject(request)
        if isinstance(subject, web.Response):
            return subject
        companion_mind = getattr(self.runtime, "companion_mind", None)
        if companion_mind is None:
            return self._json(
                {"success": False, "message": "companion memory unavailable"},
                status=503,
            )
        try:
            body = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        if not isinstance(body, dict):
            return self._json(
                {"success": False, "message": "json object required", "field": "body"},
                status=400,
            )
        action = str(body.get("action") or "").strip()
        allowed_actions = {
            "reset_relationship",
            "forget_evidence",
            "forget_theme",
            "correct_evidence",
            "set_boundary",
            "revoke_boundary",
            "purge_personal_memory",
            "revoke_adjustment",
            "set_interaction_contract",
            "revoke_interaction_contract",
            "restore_default_expression",
            "set_growth_moments_enabled",
            "set_initiative_quiet_hours",
            "confirm_candidate",
            "reject_candidate",
        }
        if action not in allowed_actions:
            return self._json(
                {"success": False, "message": "unsupported control action", "field": "action"},
                status=400,
            )
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        if not idempotency_key:
            return self._json(
                {"success": False, "message": "idempotency_key required", "field": "idempotency_key"},
                status=400,
            )
        payload = body.get("payload", {})
        if not isinstance(payload, dict):
            return self._json(
                {"success": False, "message": "payload must be an object", "field": "payload"},
                status=400,
            )
        requested_surface = str(body.get("surface") or "miniprogram").strip()
        if requested_surface != "miniprogram":
            return self._json(
                {
                    "success": False,
                    "message": "control surface does not match this endpoint",
                    "field": "surface",
                },
                status=400,
            )
        surface = "miniprogram"
        if action == "reset_relationship" and payload.get(
            "confirmed_consequences"
        ) is not True:
            return self._json(
                {
                    "success": False,
                    "message": "reset_relationship requires consequence confirmation",
                    "field": "payload.confirmed_consequences",
                },
                status=400,
            )
        if action == "purge_personal_memory" and payload.get(
            "confirmation_phrase"
        ) != "清空个人记忆":
            return self._json(
                {
                    "success": False,
                    "message": "purge_personal_memory requires the complete confirmation phrase",
                    "field": "payload.confirmation_phrase",
                },
                status=400,
            )
        subject_context = self._companion_subject_context(request, subject)
        if isinstance(subject_context, web.Response):
            return subject_context
        command_payload = {
            **payload,
            "now": datetime.now().astimezone().isoformat(),
            "idempotency_key": idempotency_key,
        }
        try:
            result = companion_mind.apply_control(
                CompanionControlCommand(
                    action=action,
                    subject=subject_context,
                    payload=command_payload,
                )
            )
        except PermissionError:
            return self._json(
                {"success": False, "message": "confirmed owner required"},
                status=403,
            )
        except CompanionIdempotencyConflict:
            return self._json(
                {
                    "success": False,
                    "message": "idempotency key reused for a different command",
                    "field": "idempotency_key",
                },
                status=409,
            )
        except ValueError as exc:
            return self._json(
                {"success": False, "message": str(exc)},
                status=400,
            )
        except CompanionUnavailableError:
            return self._json(
                {"success": False, "message": "companion memory unavailable"},
                status=503,
            )
        response = asdict(result)
        response["message"] = self._companion_control_message(action)
        return self._json(response)

    async def handle_merge_memory_subject(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        auth = self._auth_service()
        if auth is None or not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)

        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )

        subject_id = request.match_info["subject_id"]
        to_subject_id = str(payload.get("to_subject_id") or "").strip()
        if not to_subject_id:
            return self._json(
                {"success": False, "message": "to_subject_id required", "field": "to_subject_id"},
                status=400,
            )

        user = request["xiaoxin_user"]
        source = self.runtime.identity_store.get_memory_subject_for_user(subject_id, user.id)
        target = self.runtime.identity_store.get_memory_subject_for_user(to_subject_id, user.id)
        if source is None or target is None:
            return self._json({"success": False, "message": "subject not found"}, status=404)

        try:
            self.runtime.identity_store.create_subject_alias(
                source.id,
                target.id,
                "control_console_merge",
            )
        except ValueError as exc:
            return self._json({"success": False, "message": str(exc)}, status=400)
        ingress = getattr(self.runtime, "observation_ingress", None)
        if ingress is not None:
            try:
                ingress.flush_pending_for_user(user.id)
            except Exception as exc:
                self.logger.bind(tag="xiaoxin.companion_observation").error(
                    "pending observation backfill failed after subject merge: {}",
                    type(exc).__name__,
                )
        return self._json({"success": True})

    async def handle_create_event(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        try:
            payload = json.loads(await request.text())
            event_request = parse_control_event_request(payload)
            device_denied = self._deny_for_control_command_target(
                request, event_request.device_id
            )
            if device_denied is not None:
                return device_denied
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        except ControlValidationError as exc:
            return self._json(
                {"success": False, "message": str(exc), "field": exc.field},
                status=400,
            )

        try:
            record = await self.runtime.dispatcher.submit(event_request)
        except DispatcherStoppedError:
            return self._json(
                {"success": False, "message": "notification dispatcher is stopped"},
                status=503,
            )

        return self._json(
            {"delivery_id": record.delivery_id, "state": record.state.value}
        )

    async def handle_deliveries(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        owned_device_ids = self._owned_bound_device_ids(request)
        if isinstance(owned_device_ids, web.Response):
            return owned_device_ids
        return self._json(
            {
                "deliveries": [
                    record.to_dict()
                    for record in self.runtime.store.list_recent()
                    if record.device_id in owned_device_ids
                ]
            }
        )

    async def handle_delivery_detail(self, request: web.Request) -> web.Response:
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied

        delivery_id = request.match_info["delivery_id"]
        record = self.runtime.store.get(delivery_id)
        owned_device_ids = self._owned_bound_device_ids(request)
        if isinstance(owned_device_ids, web.Response):
            return owned_device_ids
        if record is None or record.device_id not in owned_device_ids:
            return self._json({"success": False, "message": "delivery not found"}, status=404)
        return self._json(record.to_dict())

    async def handle_options(self, request: web.Request) -> web.Response:
        response = web.Response(body=b"", content_type="text/plain")
        self._add_cors_headers(response)
        return response

    def _deny_if_unauthorized(self, request: web.Request) -> web.Response | None:
        return self._auth_required(request)

    def _auth_service(self):
        return getattr(self.runtime, "auth_service", None)

    def _compliance_service(self):
        return getattr(self.runtime, "compliance_service", None)

    def _require_compliance_capability(
        self,
        user_id: str,
        capability: Capability,
    ) -> web.Response | None:
        service = self._compliance_service()
        if service is None:
            return self._json(
                {"success": False, "message": "compliance unavailable"},
                status=503,
            )
        decision = service.require_capability(user_id, capability)
        if decision.allowed:
            return None
        return self._json(
            {
                "success": False,
                "code": "COMPLIANCE_GATE_DENIED",
                "message": "compliance gate denied",
                "capability": capability.value,
                "mode": decision.status.companion_mode.value,
                "reason": decision.reason,
                "requiredActions": list(decision.status.required_actions),
            },
            status=403,
        )

    def _require_device_owner_capability(
        self,
        device_id: str,
        capability: Capability,
    ) -> web.Response | None:
        identity_store = getattr(self.runtime, "identity_store", None)
        if identity_store is None:
            return self._json(
                {"success": False, "message": "compliance unavailable"},
                status=503,
            )
        device = identity_store.get_device_by_device_id(device_id)
        owner_user_id = getattr(device, "owner_user_id", None)
        if not owner_user_id:
            return self._json(
                {
                    "success": False,
                    "code": "COMPLIANCE_GATE_DENIED",
                    "message": "compliance gate denied",
                    "capability": capability.value,
                    "mode": "tool_only",
                    "reason": "device_owner_required",
                    "requiredActions": [],
                },
                status=403,
            )
        return self._require_compliance_capability(owner_user_id, capability)

    def _student_compliance_context(self, request: web.Request):
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        service = self._compliance_service()
        if service is None:
            return self._json(
                {"success": False, "message": "compliance unavailable"},
                status=503,
            )
        user = request["xiaoxin_user"]
        account = service.store.get_miniprogram_account_for_user(user.id)
        if account is None or account.account_role != "student":
            return self._json(
                {
                    "success": False,
                    "code": "student_account_required",
                    "message": "student account required",
                },
                status=403,
            )
        return service, user

    def _guardian_compliance_context(self, request: web.Request):
        denied = self._deny_if_unauthorized(request)
        if denied is not None:
            return denied
        service = self._compliance_service()
        if service is None:
            return self._json(
                {"success": False, "message": "compliance unavailable"},
                status=503,
            )
        user = request["xiaoxin_user"]
        account = service.store.get_miniprogram_account_for_user(user.id)
        if account is None or account.account_role != "guardian":
            return self._json(
                {
                    "success": False,
                    "code": "guardian_account_required",
                    "message": "guardian account required",
                },
                status=403,
            )
        return service, account

    async def _compliance_json_payload(self, request: web.Request):
        try:
            payload = json.loads(await request.text())
        except json.JSONDecodeError:
            return self._json(
                {"success": False, "message": "invalid json", "field": "body"},
                status=400,
            )
        if not isinstance(payload, dict):
            return self._json(
                {"success": False, "message": "json object required", "field": "body"},
                status=400,
            )
        return payload

    def _compliance_error_response(self, exc: Exception) -> web.Response:
        code = getattr(exc, "code", "compliance_invalid")
        status = 400
        if code in {
            "age_band_locked",
            "account_role_conflict",
            "account_user_conflict",
            "guardian_invitation_unavailable",
            "guardian_invitation_expired",
            "guardian_already_confirmed",
        }:
            status = 409
        elif code in {"guardian_invitation_not_found", "guardian_binding_not_found"}:
            status = 404
        elif code in {"guardian_account_required"}:
            status = 403
        return self._json(
            {"success": False, "code": code, "message": str(exc)},
            status=status,
        )

    @staticmethod
    def _compliance_status_payload(status) -> dict[str, Any]:
        return {
            "ageBand": status.age_band.value,
            "ageSource": status.age_source.value if status.age_source else None,
            "companionMode": status.companion_mode.value,
            "agreementRequired": status.agreement_required,
            "guardianRequired": status.guardian_required,
            "guardianConfirmed": status.guardian_confirmed,
            "guardianBindingId": status.guardian_binding_id,
            "guardianBindingStatus": status.guardian_binding_status,
            "proactiveEnabled": status.proactive_authorized,
            "memoryEnabled": status.memory_authorized,
            "proactiveEffective": status.proactive_enabled,
            "memoryEffective": status.memory_enabled,
            "requiredActions": list(status.required_actions),
            "reason": status.reason,
        }

    def _session_token(self, request: web.Request) -> str:
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization.split(" ", 1)[1].strip()
        return request.cookies.get(SESSION_COOKIE, "")

    def _current_user(self, request: web.Request):
        auth = self._auth_service()
        if auth is None:
            return None
        return auth.user_for_token(self._session_token(request))

    def _auth_required(self, request: web.Request) -> web.Response | None:
        auth = self._auth_service()
        if auth is None:
            return self._json({"success": False, "message": "auth unavailable"}, status=404)
        user = self._current_user(request)
        if user is None:
            return self._json({"success": False, "message": "login required"}, status=401)
        request["xiaoxin_user"] = user
        return None

    def _admin_required(self, request: web.Request) -> web.Response | None:
        denied = self._auth_required(request)
        if denied is not None:
            return denied
        if getattr(request["xiaoxin_user"], "role", "user") != "admin":
            return self._json(
                {
                    "success": False,
                    "code": "admin_required",
                    "message": "admin required",
                },
                status=403,
            )
        return None

    def _admin_write_required(self, request: web.Request) -> web.Response | None:
        denied = self._admin_required(request)
        if denied is not None:
            return denied
        session_token = self._session_token(request)
        expected = _csrf_token(session_token)
        supplied = str(request.headers.get("X-Xiaoxin-CSRF") or "")
        authorization = str(request.headers.get("Authorization") or "")
        cookie_token = str(request.cookies.get(CSRF_COOKIE) or "")
        cookie_valid = authorization.lower().startswith("bearer ") or hmac.compare_digest(
            cookie_token,
            supplied,
        )
        if not supplied or not cookie_valid or not hmac.compare_digest(supplied, expected):
            return self._json(
                {
                    "success": False,
                    "code": "csrf_invalid",
                    "message": "csrf validation failed",
                },
                status=403,
            )
        return None

    def _admin_memory_service(self) -> AdminMemoryQueryService:
        return AdminMemoryQueryService(
            self.runtime.identity_store,
            getattr(self.runtime, "registry", None),
            getattr(self.runtime, "companion_mind", None),
        )

    def _is_control_console_session(self, request: web.Request) -> bool:
        user = request.get("xiaoxin_user")
        if user is None:
            user = self._current_user(request)
        return getattr(user, "role", "user") == "admin"

    def _deny_for_control_command_target(
        self, request: web.Request, device_id: str
    ) -> web.Response | None:
        if self._is_control_console_session(request):
            return self._deny_if_control_target_unknown(device_id)
        return self._deny_if_device_not_owned(request, device_id)

    def _deny_for_wake_target(
        self, request: web.Request, device_id: str, tenant_id: str
    ) -> web.Response | None:
        if self._is_control_console_session(request):
            return self._deny_if_control_target_unknown(device_id)
        identity_store = getattr(self.runtime, "identity_store", None)
        if identity_store is None:
            return self._json({"success": False, "message": "auth unavailable"}, status=404)
        user = request["xiaoxin_user"]
        device = identity_store.get_device_by_device_id(device_id)
        if device is None or device.owner_user_id is None:
            return self._json({"success": False, "message": "device_not_bound"}, status=403)
        if device.tenant_id != tenant_id:
            return self._json({"success": False, "message": "tenant_mismatch"}, status=403)
        if device.owner_user_id != user.id:
            return self._json({"success": False, "message": "device_not_bound"}, status=403)
        return None

    def _deny_if_device_not_owned(
        self, request: web.Request, device_id: str
    ) -> web.Response | None:
        if not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)
        user = request["xiaoxin_user"]
        device = self.runtime.identity_store.get_device_by_device_id(device_id)
        if (
            device is None
            or device.bind_status != DEVICE_BOUND
            or device.owner_user_id != user.id
        ):
            return self._json({"success": False, "message": "device not found"}, status=404)
        return None

    def _deny_if_control_target_unknown(self, device_id: str) -> web.Response | None:
        if not device_id:
            return self._json(
                {"success": False, "message": "device_id required", "field": "device_id"},
                status=400,
            )
        identity_store = getattr(self.runtime, "identity_store", None)
        if identity_store is not None and identity_store.get_device_by_device_id(device_id):
            return None
        registry = getattr(self.runtime, "registry", None)
        if registry is not None:
            if registry.get_connection(device_id) is not None:
                return None
            if any(item["device_id"] == device_id for item in registry.list_devices()):
                return None
        return self._json({"success": False, "message": "device not found"}, status=404)

    def _owned_bound_device_ids(self, request: web.Request) -> set[str] | web.Response:
        if not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)
        user = request["xiaoxin_user"]
        return {
            device.device_id
            for device in self.runtime.identity_store.list_devices_for_user(user.id)
            if device.owner_user_id == user.id and device.bind_status == DEVICE_BOUND
        }

    def _owned_memory_subject(self, request: web.Request):
        if not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)
        try:
            user = request["xiaoxin_user"]
        except KeyError:
            return self._json({"success": False, "message": "auth unavailable"}, status=404)
        subject_id = request.match_info["subject_id"]
        subject = self.runtime.identity_store.get_memory_subject_for_user(subject_id, user.id)
        if subject is None or subject.merged_into_subject_id is not None:
            return self._json({"success": False, "message": "subject not found"}, status=404)
        return subject

    def _companion_subject_context(
        self, request: web.Request, subject: Any
    ) -> CompanionSubjectContext | web.Response:
        user = request["xiaoxin_user"]
        pet = self.runtime.identity_store.get_personal_pet_for_user(user.id)
        if pet is None:
            return self._json(
                {"success": False, "message": "personal pet not found"},
                status=404,
            )
        profile = self.runtime.identity_store.get_student_profile_for_user(user.id)
        return build_companion_subject_context(
            owner_user_id=user.id,
            pet_id=pet.id,
            memory_subject_id=subject.id,
            subject_kind=subject.kind,
            raw_grade=profile.get("grade") if profile is not None else None,
        )

    def _miniprogram_confirmed_subject(self, request: web.Request):
        identity_store = getattr(self.runtime, "identity_store", None)
        if identity_store is None:
            return self._json({"success": False, "message": "auth unavailable"}, status=404)
        user = request["xiaoxin_user"]
        candidates = []
        for subject in identity_store.list_memory_subjects_for_user(user.id):
            if (
                subject.kind != "user_speaker"
                or subject.merged_into_subject_id is not None
                or not subject.speaker_profile_id
            ):
                continue
            speaker = identity_store.get_speaker_profile(subject.speaker_profile_id)
            device = identity_store.get_device_by_device_id(subject.device_id)
            if (
                speaker is not None
                and speaker.status == SPEAKER_CONFIRMED
                and speaker.owner_user_id == user.id
                and device is not None
                and device.owner_user_id == user.id
                and device.bind_status == DEVICE_BOUND
            ):
                candidates.append(subject)
        if not candidates:
            return self._json(
                {
                    "success": False,
                    "code": "confirmed_subject_required",
                    "message": "confirmed companion subject required",
                },
                status=409,
            )
        if len(candidates) > 1:
            return self._json(
                {
                    "success": False,
                    "code": "subject_selection_required",
                    "message": "multiple confirmed companion subjects require selection",
                },
                status=409,
            )
        return candidates[0]

    @staticmethod
    def _companion_control_message(action: str) -> str:
        if action == "reset_relationship":
            return (
                "关系已重置：保留学生资料、账号、设备、明确边界与用户事实；"
                "旧关系记忆已停用。"
            )
        if action == "purge_personal_memory":
            return (
                "陪伴记忆已清除：账号、设备绑定、个人小芯归属和学生资料仍保留。"
            )
        if action == "confirm_candidate":
            return "候选记忆已确认，之后可以参与安全召回。"
        if action == "reject_candidate":
            return "候选记忆已拒绝，不会参与后续召回。"
        if action == "revoke_adjustment":
            return "已撤销这项相处中学会的表达，其他设置保持不变。"
        if action == "set_interaction_contract":
            return "长期相处方式已保存，并替代同一项隐式调整。"
        if action == "revoke_interaction_contract":
            return "已撤销这项长期相处方式，其他设置保持不变。"
        if action == "restore_default_expression":
            return "已恢复默认表达；出生气质、明确设置和共同经历仍保留。"
        return "陪伴记忆控制已执行。"

    def _current_student_profile(self, request: web.Request):
        if not hasattr(self.runtime, "identity_store"):
            return self._json({"success": False, "message": "auth unavailable"}, status=404)
        user = request["xiaoxin_user"]
        profile = self.runtime.identity_store.get_student_profile_for_user(user.id)
        if profile is None:
            return self._json({"success": False, "message": "profile not found"}, status=404)
        return profile

    def _sync_companion_academic_stage(
        self,
        user: Any,
        profile: dict[str, object],
        *,
        update: dict[str, Any] | None = None,
    ) -> None:
        companion_mind = getattr(self.runtime, "companion_mind", None)
        identity_store = getattr(self.runtime, "identity_store", None)
        if companion_mind is None or identity_store is None:
            return
        pet = identity_store.get_personal_pet_for_user(user.id)
        if pet is None:
            return
        update = update or {}
        now = str(profile.get("updated_at") or datetime.now().astimezone().isoformat())
        transition_kind = update.get("transition_kind") or update.get("transitionKind")
        if transition_kind is None and "major" in update and not (
            {"grade", "academic_status", "academicStatus"} & update.keys()
        ):
            transition_kind = "major_change"
        for subject in identity_store.list_memory_subjects_for_user(user.id):
            if subject.kind != "user_speaker" or subject.merged_into_subject_id is not None:
                continue
            context = build_companion_subject_context(
                owner_user_id=user.id,
                pet_id=pet.id,
                memory_subject_id=subject.id,
                subject_kind=subject.kind,
                raw_grade=profile.get("grade"),
            )
            companion_mind.apply_control(
                CompanionControlCommand(
                    action="sync_academic_stage",
                    subject=context,
                    payload={
                        "now": now,
                        "effective_at": str(
                            update.get("effective_at")
                            or update.get("effectiveAt")
                            or now
                        ),
                        "academic_status": str(
                            profile.get("academic_status") or "unknown"
                        ),
                        "transition_kind": transition_kind,
                        "source_revision": int(profile["revision"]),
                        "clear_stage": update.get(
                            "clear_stage", update.get("clearGrade", False)
                        ),
                    },
                )
            )
    def _student_profile_payload(self, profile: dict[str, object]) -> dict[str, object]:
        return {
            "openid": profile["openid"],
            "nickname": profile["nickname"],
            "student_no": profile["student_no"],
            "college": profile["college"],
            "major": profile["major"],
            "class_name": profile["class_name"],
            "grade": profile["grade"],
            "academic_status": profile["academic_status"],
            "revision": profile["revision"],
        }

    def _validate_semester_payload(self, payload: dict[str, Any]) -> None:
        start_date = str(payload.get("startDate") or payload.get("start_date") or "")
        datetime.strptime(start_date, "%Y-%m-%d")
        total_weeks = int(payload.get("totalWeeks") or payload.get("total_weeks") or 0)
        if total_weeks < 1 or total_weeks > 30:
            raise ValueError("totalWeeks must be 1-30")

    def _validate_course_reminder_settings_payload(
        self, payload: dict[str, Any]
    ) -> None:
        if "remindBeforeMin" not in payload and "remind_before_min" not in payload:
            raise ValueError("remindBeforeMin required")
        normalize_course_remind_before_min(
            payload.get("remindBeforeMin", payload.get("remind_before_min"))
        )

    def _student_semester_payload(self, semester: dict[str, Any]) -> dict[str, Any]:
        return {
            "label": semester["label"],
            "startDate": semester["start_date"],
            "totalWeeks": semester["total_weeks"],
        }

    def _course_reminder_settings_payload(
        self, settings: dict[str, Any]
    ) -> dict[str, Any]:
        return {"remindBeforeMin": settings["remind_before_min"]}

    def _validate_course_payload(self, payload: dict[str, Any]) -> None:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValueError("title required")
        weekday = int(payload.get("weekday") or 0)
        start_section = int(payload.get("startSection") or payload.get("start_section") or 0)
        end_section = int(payload.get("endSection") or payload.get("end_section") or 0)
        if weekday < 1 or weekday > 7:
            raise ValueError("weekday must be 1-7")
        if start_section < 1 or end_section < start_section:
            raise ValueError("invalid course sections")

    @staticmethod
    def _normalize_explicit_companion_observation(
        kind: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, object], str]:
        if kind in {"goal_set", "goal_completed"}:
            goal_id = str(payload.get("goalId") or "").strip()
            title = str(payload.get("title") or "").strip()
            if not goal_id or not title:
                raise ValueError("goalId and title required")
            normalized: dict[str, object] = {
                "goal_id": goal_id,
                "title": title,
                "status": "completed" if kind == "goal_completed" else "active",
            }
            target_at = str(payload.get("targetAt") or "").strip()
            if target_at:
                normalize_todo_due_at(target_at)
                normalized["target_at"] = target_at
            if kind == "goal_completed":
                normalized["completion_source"] = "explicit_user_action"
            return goal_id, normalized, (
                "用户明确完成了一项目标。"
                if kind == "goal_completed"
                else "用户明确设定了一项目标。"
            )
        if kind in {"future_event_set", "future_event_cancelled"}:
            event_id = str(payload.get("eventId") or "").strip()
            title = str(payload.get("title") or "").strip()
            scheduled_at = normalize_todo_due_at(payload.get("scheduledAt"))
            if not event_id or not title:
                raise ValueError("eventId and title required")
            return event_id, {
                "event_id": event_id,
                "title": title,
                "scheduled_at": scheduled_at,
                "status": (
                    "cancelled" if kind == "future_event_cancelled" else "planned"
                ),
            }, (
                "用户明确取消了一项未来事件。"
                if kind == "future_event_cancelled"
                else "用户明确记录了一项未来事件。"
            )
        if kind == "boundary_set":
            boundary_key = str(payload.get("boundaryKey") or "").strip()
            if not boundary_key or "value" not in payload:
                raise ValueError("boundaryKey and value required")
            return boundary_key, {
                "boundary_key": boundary_key,
                "value": payload["value"],
            }, "用户明确设置了一项陪伴边界。"
        if kind == "companion_feedback":
            feedback_id = str(payload.get("feedbackId") or "").strip()
            interaction_ref = str(payload.get("interactionRef") or "").strip()
            signal = str(payload.get("signal") or "").strip()
            if not feedback_id or not interaction_ref:
                raise ValueError("feedbackId and interactionRef required")
            if signal not in {
                "helpful",
                "not_helpful",
                "too_proactive",
                "too_personal",
            }:
                raise ValueError("unsupported companion feedback signal")
            return feedback_id, {
                "feedback_id": feedback_id,
                "interaction_ref": interaction_ref,
                "signal": signal,
            }, "用户对小芯给出了明确反馈。"
        raise ValueError("unsupported companion observation kind")

    def _student_course_payload(self, course: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": course["id"],
            "title": course["title"],
            "classroom": course["classroom"],
            "teacher": course["teacher"],
            "weekday": course["weekday"],
            "startSection": course["start_section"],
            "endSection": course["end_section"],
            "weekRange": course["week_range"],
            "startsAt": course["starts_at"],
            "endsAt": course["ends_at"],
            "notes": course["notes"],
        }

    def _validate_todo_payload(
        self, payload: dict[str, Any], *, partial: bool
    ) -> None:
        if not partial or "title" in payload:
            title = str(payload.get("title") or "").strip()
            if not title:
                raise ValueError("title required")
        if not partial or "dueAt" in payload or "due_at" in payload:
            normalize_todo_due_at(payload.get("dueAt") or payload.get("due_at"))
        if "status" in payload:
            status = str(payload.get("status") or "").strip()
            if status not in {"pending", "done"}:
                raise ValueError("status must be pending or done")

    def _student_todo_payload(self, todo: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": todo["id"],
            "title": todo["title"],
            "dueAt": todo["due_at"],
            "notes": todo["notes"],
            "status": todo["status"],
            "reminderStatus": todo["reminder_status"],
            "source": todo["source"],
            "sourceDeviceId": todo["source_device_id"],
            "createdAt": todo["created_at"],
            "updatedAt": todo["updated_at"],
        }

    def _observe_todo_event(
        self,
        user_id: str,
        todo: dict[str, Any],
        kind: str,
        *,
        previous_status: str | None = None,
        occurred_at: str | None = None,
    ) -> None:
        ingress = getattr(self.runtime, "observation_ingress", None)
        if ingress is None:
            return
        observed_at = str(
            occurred_at
            or todo.get("updated_at")
            or todo.get("created_at")
            or datetime.now().astimezone().isoformat()
        )
        status = str(todo.get("status") or "")
        payload: dict[str, object] = {
            "todo_id": str(todo["id"]),
            "title": str(todo["title"]),
            "due_at": str(todo["due_at"]),
            "status": status,
        }
        notes = str(todo.get("notes") or "").strip()
        if notes:
            payload["notes"] = notes
        if kind == "todo_completed":
            payload["completion_source"] = "explicit_user_action"
        if previous_status:
            payload["previous_status"] = previous_status
        summaries = {
            "todo_created": "用户创建了一项未来待办。",
            "todo_updated": "用户更新了一项未来待办。",
            "todo_completed": "用户明确完成了一项待办。",
            "todo_deleted": "用户删除了一项待办。",
        }
        try:
            ingress.observe_user_event(
                user_id=user_id,
                idempotency_key=f"{kind}:{todo['id']}:{observed_at}",
                kind=kind,
                source_kind="miniprogram_todo",
                source_ref=str(todo["id"]),
                occurred_at=observed_at,
                payload=payload,
                safe_summary=summaries[kind],
            )
        except Exception as exc:
            self.logger.bind(tag="xiaoxin.companion_observation").error(
                "todo observation failed: {}",
                type(exc).__name__,
            )

    def _observe_course_event(
        self,
        user_id: str,
        course: dict[str, Any],
        kind: str,
        *,
        occurred_at: str | None = None,
    ) -> None:
        ingress = getattr(self.runtime, "observation_ingress", None)
        if ingress is None:
            return
        observed_at = str(
            occurred_at
            or course.get("updated_at")
            or course.get("created_at")
            or datetime.now().astimezone().isoformat()
        )
        payload: dict[str, object] = {
            "course_id": str(course["id"]),
            "title": str(course["title"]),
            "classroom": str(course.get("classroom") or ""),
            "teacher": str(course.get("teacher") or ""),
            "weekday": int(course["weekday"]),
            "start_section": int(course["start_section"]),
            "end_section": int(course["end_section"]),
            "week_range": str(course.get("week_range") or ""),
            "starts_at": str(course.get("starts_at") or ""),
            "ends_at": str(course.get("ends_at") or ""),
        }
        notes = str(course.get("notes") or "").strip()
        if notes:
            payload["notes"] = notes
        summaries = {
            "course_created": "用户创建了一门课程。",
            "course_updated": "用户更新了一门课程。",
            "course_deleted": "用户删除了一门课程。",
        }
        try:
            ingress.observe_user_event(
                user_id=user_id,
                idempotency_key=f"{kind}:{course['id']}:{observed_at}",
                kind=kind,
                source_kind="miniprogram_course",
                source_ref=str(course["id"]),
                occurred_at=observed_at,
                payload=payload,
                safe_summary=summaries[kind],
            )
        except Exception as exc:
            self.logger.bind(tag="xiaoxin.companion_observation").error(
                "course observation failed: {}",
                type(exc).__name__,
            )

    def _miniprogram_diagnostic_checks(
        self,
        request: web.Request,
        user: Any,
        date_text: str,
    ) -> list[dict[str, Any]]:
        profile = self.runtime.identity_store.get_student_profile_for_user(user.id)
        checks = [
            self._diagnostic_check(
                "session",
                "ok",
                "session token accepted",
                {
                    "userId": user.id,
                    "username": user.username,
                    "openid": profile["openid"] if profile else "",
                },
            )
        ]

        if profile is None:
            checks.append(
                self._diagnostic_check(
                    "profile",
                    "error",
                    "profile not found",
                    {"profileExists": False},
                )
            )
        else:
            checks.append(
                self._diagnostic_check(
                    "profile",
                    "ok",
                    "profile available",
                    {
                        "profileExists": True,
                        "profile": self._student_profile_payload(profile),
                    },
                )
            )

        device = self._miniprogram_bound_device(request)
        if device is None:
            checks.append(
                self._diagnostic_check(
                    "device",
                    "warning",
                    "no bound device",
                    {"bound": False, "device": self._miniprogram_device_payload(None)},
                )
            )
        else:
            checks.append(
                self._diagnostic_check(
                    "device",
                    "ok",
                    "bound device available",
                    {
                        "bound": True,
                        "device": self._miniprogram_device_payload(device),
                    },
                )
            )

        semester = self.runtime.identity_store.get_student_semester(user.id)
        checks.append(
            self._diagnostic_check(
                "semester",
                "ok",
                "semester available",
                self._student_semester_payload(semester),
            )
        )

        courses = self.runtime.identity_store.list_student_courses(user.id)
        checks.append(
            self._diagnostic_check(
                "courses",
                "ok",
                "courses loaded",
                {"count": len(courses)},
            )
        )

        try:
            curriculum = self._curriculum_overview(user.id, date_text)
        except ValueError as exc:
            checks.append(
                self._diagnostic_check(
                    "curriculumOverview",
                    "error",
                    str(exc),
                    {"date": date_text},
                )
            )
        else:
            checks.append(
                self._diagnostic_check(
                    "curriculumOverview",
                    "ok",
                    "curriculum overview available",
                    {
                        "date": curriculum["date"],
                        "currentWeek": curriculum["currentWeek"],
                        "todayCourseCount": len(curriculum["todayCourses"]),
                        "conflictCount": curriculum["conflictCount"],
                    },
                )
            )

        return checks

    def _miniprogram_notification_records(
        self, device_ids: set[str]
    ) -> list[Any]:
        history_store = getattr(self.runtime, "notification_history_store", None)
        if callable(getattr(history_store, "list_for_device_ids", None)):
            return history_store.list_for_device_ids(device_ids)
        store = getattr(self.runtime, "store", None)
        if not callable(getattr(store, "list_recent", None)):
            return []
        return [
            record
            for record in store.list_recent()
            if getattr(record, "device_id", "") in device_ids
        ]

    def _is_companion_initiative_record(self, record: Any) -> bool:
        if isinstance(record, dict):
            request = record.get("request") or {}
            tag = request.get("tag") if isinstance(request, dict) else ""
        else:
            request = getattr(record, "request", None)
            tag = getattr(request, "tag", "")
        return str(tag or "").startswith("companion:")

    def _diagnostic_check(
        self,
        name: str,
        status: str,
        message: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "name": name,
            "status": status,
            "message": message,
            "details": details,
        }

    def _miniprogram_companion_history_payload(self, record: Any) -> dict[str, Any]:
        payload = self._miniprogram_notification_history_payload(record)
        payload["type"] = "companion_initiative"
        payload["source"] = "companion"
        return payload

    def _miniprogram_notification_history_payload(self, record: Any) -> dict[str, Any]:
        if isinstance(record, dict):
            request = record["request"]
            delivery_id = record["delivery_id"]
            device_id = record["device_id"]
            event = str(record["event"])
            title = str(request.get("title") or "")
            body = str(request.get("body") or "")
            state = str(record["state"])
            reason = str(record.get("reason") or "")
            created_at = str(record["created_at"])
            updated_at = str(record["updated_at"])
            timeline = list(record.get("timeline") or [])
        else:
            request = record.request
            delivery_id = record.delivery_id
            device_id = record.device_id
            event = request.event.value
            title = request.title
            body = request.body
            state = record.state.value
            reason = record.reason.value if record.reason else ""
            created_at = record.created_at
            updated_at = record.updated_at
            timeline = [entry.to_dict() for entry in record.timeline]
        return {
            "id": delivery_id,
            "deliveryId": delivery_id,
            "deviceId": device_id,
            "type": event,
            "title": title,
            "body": body,
            "status": self._miniprogram_notification_status(record),
            "deliveryState": state,
            "reason": reason,
            "source": "hardware_delivery",
            "occurredAt": created_at,
            "createdAt": created_at,
            "updatedAt": updated_at,
            "timeline": timeline,
        }

    def _miniprogram_notification_status(self, record: Any) -> str:
        if isinstance(record, dict):
            state = str(record["state"])
            event = str(record["event"])
            reason = str(record.get("reason") or "")
        else:
            state = record.state.value
            event = record.event.value
            reason = record.reason.value if record.reason else ""
        if state == XiaoxinDeliveryState.DONE.value:
            return "announced"
        if state == XiaoxinDeliveryState.FAILED.value:
            if (
                event == XiaoxinEvent.COURSE_REMINDER.value
                and reason == XiaoxinFailureReason.DEVICE_OFFLINE.value
            ):
                return "missed"
            if (
                event == XiaoxinEvent.TODO_REMINDER.value
                and reason == XiaoxinFailureReason.DEVICE_OFFLINE.value
            ):
                return "pending_redelivery"
            return "failed"
        return "pending"

    def _curriculum_overview(self, user_id: str, date_text: str) -> dict[str, Any]:
        if self.overview_service is None:
            raise RuntimeError("overview projection unavailable")
        return self.overview_service.build_curriculum_overview(
            user_id, date_text, include_started=False
        )

    def _student_overview(
        self,
        user_id: str,
        date_text: str,
        *,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        if self.overview_service is None:
            raise RuntimeError("overview projection unavailable")
        overview = dict(
            self.overview_service.build_student_overview(
                user_id,
                date_text,
                device_id=device_id,
                include_started=False,
            )
        )
        notification_records = [
            record
            for record in self._notification_records_for_user(user_id)
            if not self._is_companion_initiative_record(record)
        ]
        latest_notification = (
            self._miniprogram_notification_history_payload(notification_records[0])
            if notification_records
            else None
        )
        overview["latestNotification"] = latest_notification
        today_summary = dict(overview.get("todaySummary") or {})
        today_summary["latestNotificationState"] = self._latest_notification_state(
            latest_notification
        )
        overview["todaySummary"] = today_summary
        return overview

    def _notification_records_for_user(self, user_id: str) -> list[Any]:
        device_ids = [
            device.device_id
            for device in self.runtime.identity_store.list_devices_for_user(user_id)
            if device.owner_user_id == user_id and device.bind_status == DEVICE_BOUND
        ]
        if not device_ids:
            return []
        history_store = getattr(self.runtime, "notification_history_store", None)
        if callable(getattr(history_store, "list_for_device_ids", None)):
            return history_store.list_for_device_ids(device_ids)
        store = getattr(self.runtime, "store", None)
        if not callable(getattr(store, "list_recent", None)):
            return []
        return [
            record
            for record in store.list_recent()
            if getattr(record, "device_id", "") in device_ids
        ]

    def _latest_notification_state(self, latest_notification: dict[str, Any] | None) -> str:
        if latest_notification is None:
            return "暂无通知"
        labels = {
            "announced": "最新通知已播报",
            "missed": "有通知已错过",
            "pending_redelivery": "有通知待补发",
            "pending": "有通知待播报",
            "failed": "通知投递失败",
        }
        return labels.get(str(latest_notification.get("status") or ""), "最新通知待查看")

    def _demo_overview(self, device_id: str) -> dict[str, Any]:
        demo_data = self.demo_data_store.load()
        overview = demo_data.get("overview", {})
        device = None
        if hasattr(self.runtime, "identity_store"):
            device = self.runtime.identity_store.get_device_by_device_id(device_id)

        return {
            "source": "demo",
            "date": local_date_text(),
            "generatedAt": local_datetime().isoformat(timespec="seconds"),
            "device": self._miniprogram_device_payload(device),
            "weather": overview.get("weather", self._empty_weather_overview()),
            "course": overview.get("course", self._course_overview_card({}, has_configured_courses=False)),
            "todo": overview.get("todo", self._empty_todo_overview()),
        }

    def _empty_weather_overview(self) -> dict[str, Any]:
        return {
            "configured": False,
            "available": False,
            "summary": "未配置位置",
            "detail": "设置位置后显示天气",
        }

    def _empty_todo_overview(self) -> dict[str, Any]:
        return {
            "configured": False,
            "count": 0,
            "detail": "暂无待办",
        }

    def _todo_overview_card(self, user_id: str, date_text: str) -> dict[str, Any]:
        todos = self.runtime.identity_store.list_student_todos(user_id)
        pending = [todo for todo in todos if todo["status"] == "pending"]
        future_boundary = f"{date_text}T00:00:00"
        upcoming = [
            todo
            for todo in pending
            if str(todo.get("due_at") or "") >= future_boundary
        ]
        next_todo = upcoming[0] if upcoming else None
        if next_todo is None:
            return {
                "configured": bool(todos),
                "count": len(pending),
                "detail": "暂无待提醒事项" if todos else "暂无待办",
                "nextTodo": None,
            }

        return {
            "configured": True,
            "count": len(pending),
            "detail": self._todo_detail_text(next_todo),
            "nextTodo": self._student_todo_payload(next_todo),
        }

    def _todo_detail_text(self, todo: dict[str, Any]) -> str:
        due_at = str(todo.get("due_at") or "").strip()
        time_text = due_at[11:16] if len(due_at) >= 16 and "T" in due_at else due_at
        title = str(todo.get("title") or "").strip()
        return f"{time_text} {title}".strip()

    def _course_overview_card(
        self,
        curriculum: dict[str, Any],
        *,
        has_configured_courses: bool,
    ) -> dict[str, Any]:
        next_course = curriculum.get("nextCourse")
        if not next_course:
            return {
                "configured": has_configured_courses,
                "available_today": False,
                "title": "暂无课程",
                "detail": "今日无课" if has_configured_courses else "在小程序中添加课表后显示",
            }

        starts_at = str(next_course.get("startsAt") or "").strip()
        start_section = int(next_course.get("startSection") or 0)
        end_section = int(next_course.get("endSection") or 0)
        section_text = (
            f"第{start_section}-{end_section}节"
            if start_section and end_section
            else ""
        )
        title_suffix = starts_at or section_text
        classroom = str(next_course.get("classroom") or "").strip()
        detail_parts = [part for part in (classroom, section_text) if part]
        return {
            "configured": True,
            "available_today": True,
            "title": (
                f"{next_course.get('title', '')} {title_suffix}".strip()
                if title_suffix
                else str(next_course.get("title") or "").strip()
            ),
            "detail": " · ".join(detail_parts),
            "course": next_course,
        }

    def _overview_update_payload(
        self,
        device_id: str,
        overview: dict[str, Any],
        *,
        notifications: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "xiaoxin_overview_update",
            "device_id": device_id,
            "generated_at": overview["generatedAt"],
            "overview": overview,
            "weather": overview["weather"],
            "course": overview["course"],
            "todo": overview["todo"],
            "device": overview["device"],
            "notifications": notifications or [],
        }

    def _course_active_in_week(
        self,
        course: dict[str, Any],
        current_week: int | None,
    ) -> bool:
        if current_week is None:
            return False

        week_range = str(course.get("week_range") or "").strip()
        if not week_range:
            return True
        if week_range == "非本周" or "非本" in week_range:
            return False

        numeric_ranges = [
            (int(match.group(1)), int(match.group(2) or match.group(1)))
            for match in re.finditer(r"(\d+)(?:\s*[-~—至到]\s*(\d+))?", week_range)
        ]
        numeric_ranges = [
            (start, end)
            for start, end in numeric_ranges
            if start > 0 and end >= start
        ]
        if numeric_ranges:
            return any(start <= current_week <= end for start, end in numeric_ranges)
        if week_range == "非本周":
            return False

        normalized = week_range.removeprefix("第").removesuffix("周")
        ranges: list[tuple[int, int]] = []
        for part in normalized.replace("，", ",").replace("、", ",").split(","):
            text = part.strip()
            if not text:
                continue
            if "-" in text:
                start_text, end_text = text.split("-", 1)
            else:
                start_text = end_text = text
            try:
                start_week = int(start_text)
                end_week = int(end_text)
            except ValueError:
                continue
            if start_week > 0 and end_week >= start_week:
                ranges.append((start_week, end_week))

        if not ranges:
            return True
        return any(start <= current_week <= end for start, end in ranges)

    def _course_conflict_map(
        self, courses: list[dict[str, Any]]
    ) -> dict[str, set[str]]:
        conflicts: dict[str, set[str]] = {
            str(course["id"]): set() for course in courses
        }
        for index, left in enumerate(courses):
            for right in courses[index + 1 :]:
                if self._course_sections_overlap(left, right):
                    left_id = str(left["id"])
                    right_id = str(right["id"])
                    conflicts[left_id].add(right_id)
                    conflicts[right_id].add(left_id)
        return conflicts

    def _course_sections_overlap(
        self, left: dict[str, Any], right: dict[str, Any]
    ) -> bool:
        left_start = int(left.get("start_section") or 0)
        left_end = int(left.get("end_section") or 0)
        right_start = int(right.get("start_section") or 0)
        right_end = int(right.get("end_section") or 0)
        if min(left_start, left_end, right_start, right_end) <= 0:
            return False
        return max(left_start, right_start) <= min(left_end, right_end)

    def _miniprogram_bound_device(self, request: web.Request):
        if not hasattr(self.runtime, "identity_store"):
            return None
        user = request["xiaoxin_user"]
        devices = self.runtime.identity_store.list_devices_for_user(user.id)
        for device in devices:
            if device.owner_user_id == user.id and device.bind_status == DEVICE_BOUND:
                return device
        return None

    def _miniprogram_device_payload(self, device: Any | None) -> dict[str, Any]:
        if device is None:
            return {
                "bound": False,
                "deviceId": "",
                "name": "",
                "state": "offline",
                "batteryLevel": None,
                "batteryPercent": None,
                "firmwareVersion": "",
                "lastSeenAt": "",
                "boundAt": "",
            }

        runtime_state = {}
        if hasattr(self.runtime, "registry"):
            runtime_state = {
                item["device_id"]: item for item in self.runtime.registry.list_devices()
            }.get(device.device_id, {})

        battery_percent = runtime_state.get("battery_percent")
        if battery_percent is None:
            battery_percent = runtime_state.get("battery")

        return {
            "bound": True,
            "deviceId": device.device_id,
            "name": device.display_name,
            "state": runtime_state.get("state", "offline"),
            "batteryLevel": runtime_state.get("battery_level"),
            "batteryPercent": battery_percent,
            "firmwareVersion": runtime_state.get("firmware_version", ""),
            "lastSeenAt": runtime_state.get("last_seen_at") or device.last_seen_at or "",
            "boundAt": getattr(device, "bound_at", None) or "",
        }

    def _demo_data_path(self) -> Path:
        demo_data_path = self.config.get("xiaoxin_control", {}).get(
            "demo_data_path",
            "data/xiaoxin_demo_data.json",
        )
        resolved = Path(demo_data_path)
        if not resolved.is_absolute():
            resolved = Path(config_loader.get_project_dir()) / resolved
        return resolved.resolve()

    def _user_payload(self, user: Any) -> dict[str, Any]:
        return {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": getattr(user, "role", "user"),
            "created_at": user.created_at,
            "last_login_at": user.last_login_at,
        }

    def _set_session_cookie(self, response: web.Response, token: str) -> None:
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax", path="/")
        response.set_cookie(
            CSRF_COOKIE,
            _csrf_token(token),
            httponly=False,
            samesite="Strict",
            path="/",
        )

    def _add_cors_headers(self, response: web.Response) -> None:
        super()._add_cors_headers(response)
        response.headers["Access-Control-Allow-Headers"] = (
            "client-id, content-type, device-id, device-username, authorization, "
            "x-xiaoxin-csrf"
        )
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"

    def _json(self, body: dict[str, Any], status: int = 200) -> web.Response:
        response = web.Response(
            text=json.dumps(body, ensure_ascii=False, separators=(",", ":")),
            status=status,
            content_type="application/json",
        )
        self._add_cors_headers(response)
        return response
