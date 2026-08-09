from pathlib import Path


CONTROL_HTML = (
    Path(__file__).resolve().parents[2]
    / "core"
    / "api"
    / "static"
    / "xiaoxin_control.html"
)
CONTROL_HANDLER = CONTROL_HTML.parents[1] / "xiaoxin_control_handler.py"


def test_control_handler_does_not_use_legacy_memory_files():
    source = CONTROL_HANDLER.read_text(encoding="utf-8")

    assert "core.xiaoxin.memory.legacy_memory" not in source
    assert "core.xiaoxin.memory.subject_summary" not in source
    assert "_memory_dir(" not in source


def test_control_console_uses_time_only_for_course_start_time():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'name="starts_at" type="time" value="10:10"' in html
    assert "2026-07-03T10:10" not in html


def test_control_console_submits_course_start_time_as_hh_mm():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert "function formatTimeForInput(value)" in html
    assert "payload.starts_at = normalizeDateTimeInputToIso(payload.starts_at);" not in html


def test_control_console_uses_datetime_picker_for_todo_due_at():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'name="due_at" type="datetime-local" value="2026-07-03T18:00"' in html
    assert 'name="due_at" value="2026-07-03T18:00:00+08:00"' not in html


def test_control_console_submits_todo_due_at_with_timezone_offset():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert "function normalizeLocalDateTimeToOffset(value)" in html
    assert "payload.due_at = normalizeLocalDateTimeToOffset(payload.due_at);" in html


def test_control_console_todo_template_enables_tts_by_default():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    todo_template = html.split('} else if (templateName === "todo") {', 1)[1].split(
        "} else {", 1
    )[0]
    assert 'form.speak.value = "true";' in todo_template
    assert 'form.speak.value = "false";' not in todo_template


def test_control_console_labels_manual_events_as_immediate_dispatch():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert "<h2>事件立即下发</h2>" in html
    assert "<span>立即下发测试</span>" in html
    assert 'id="sendBtn">立即发送到小芯</button>' in html
    assert "已立即创建投递" in html


def test_control_console_time_fields_are_labeled_as_display_content():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert '<label class="course-field">展示开始时间' in html
    assert '<label class="course-field">展示提前分钟' in html
    assert '<label class="todo-field">展示截止时间' in html
    assert '<label class="course-field">开始时间' not in html
    assert '<label class="course-field">提前分钟' not in html
    assert '<label class="todo-field">截止时间' not in html


def test_memory_subject_rows_use_single_column_layout_to_prevent_wrapped_ids():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert ".subject-row { grid-template-columns: minmax(0, 1fr); align-items: start; }" in html
    assert (
        ".subject-row .inline-actions { justify-content: flex-start; min-width: 0; width: 100%; }"
        in html
    )
    assert ".subject-row .inline-actions select { flex: 1 1 220px; max-width: 100%; }" in html


def test_control_console_renders_operator_memory_diagnostics_workbench():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'id="memoryIdentityStrip"' in html
    assert 'id="memoryTimeline"' in html
    assert 'id="memoryLineage"' in html
    assert 'id="memoryEpochs"' in html
    assert 'id="memoryHealth"' in html
    assert "diagnostics.evidence_timeline" in html
    assert "diagnostics.lineage" in html
    assert "diagnostics.epochs" in html
    assert "diagnostics.health" in html
    assert 'data-evidence-status="${escapeHtml(item.status)}"' in html


def test_control_console_surfaces_a_readable_user_profile_before_diagnostics():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert '<h4>用户画像</h4>' in html
    assert 'id="memoryPreferredName"' in html
    assert 'id="memoryConfirmedFactCount"' in html
    assert 'id="memoryCandidateFactCount"' in html
    assert 'id="memoryLastFactAt"' in html
    assert 'id="memoryProfileFacts"' in html
    assert "function renderMemoryProfile(timeline)" in html
    assert 'item.status === "active" && item.ownership_scope === "user"' in html
    assert 'item.status === "candidate" && item.ownership_scope === "user"' in html
    assert 'preferred_name: "偏好称呼"' in html


def test_control_console_prioritizes_memory_readiness_over_empty_diagnostics():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'id="memoryReadiness"' in html
    assert 'id="memoryReadinessTitle"' in html
    assert 'id="memoryReadinessMessage"' in html
    assert 'id="memoryReadinessActions"' in html
    assert 'id="memoryUsableContent"' in html
    assert '<details id="memoryAdvancedDiagnostics"' in html
    assert '<summary>' in html
    assert "高级诊断" in html
    assert '<details id="memoryAdvancedDiagnostics" open' not in html
    assert "function renderMemoryReadiness(payload, selectedSubject, timeline)" in html
    assert 'state.subjectAdminDetail?.readiness' in html
    assert 'readiness.code === "ready"' in html
    assert 'Boolean(payload.pet_id && payload.memory_subject_id)' not in html
    assert "当前主体不能形成个人长期记忆" in html
    assert "说话人尚未确认" in html
    assert "设备尚未绑定" in html
    assert "个人小芯尚未激活" in html
    assert 'data-switch-memory-subject="${escapeHtml(subject.id)}"' in html
    assert "memoryUsableContentEl.classList.toggle(\"hidden\", !isReady);" in html


def test_control_console_uses_explicit_admin_read_apis_and_backend_recommendation():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'apiFetch("/api/xiaoxin/admin/devices")' in html
    assert 'apiFetch("/api/xiaoxin/admin/speakers")' in html
    assert 'apiFetch("/api/xiaoxin/admin/memory-subjects")' in html
    assert "/api/xiaoxin/admin/memory-subjects/${encodeURIComponent(state.selectedSubjectId)}" in html
    assert "data.recommended_subject_id || state.memorySubjects[0].id" in html
    assert "state.subjectMemory = data.projection || null" in html
    assert "async function refresh(includeMemory = true)" in html
    assert "refresh(false).catch" in html
    assert "/api/xiaoxin/admin/memory-subjects/${encodeURIComponent(subjectId)}/merge" in html
    assert 'subject.owner?.id === current.owner?.id' in html
    assert 'subject.kind === current.kind' in html
    assert 'state.subjectAdminDetail?.readiness?.code !== "ready"' in html


def test_control_console_renders_safe_pending_observation_diagnostics():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'id="memoryPendingObservations"' in html
    assert "diagnostics.pending_observations || []" in html
    for safe_field in (
        "observation_id",
        "kind",
        "source_kind",
        "source_ref",
        "safe_summary",
        "occurred_at",
        "queued_reason",
        "status",
        "attempt_count",
        "last_error_code",
        "expires_at",
    ):
        assert f"item.{safe_field}" in html
    pending_renderer = html.split(
        "memoryPendingObservationsEl.innerHTML =", 1
    )[1].split("memoryRetrievalAuditsEl.innerHTML =", 1)[0]
    assert "payload_json" not in pending_renderer
    assert "item.payload" not in pending_renderer


def test_control_console_renders_safe_retrieval_audits_without_query_text():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'id="memoryRetrievalAudits"' in html
    assert "diagnostics.retrieval_audits || []" in html
    assert "item.query_digest" in html
    assert "item.selected_evidence_ids" in html
    assert "item.score_details" in html
    retrieval_renderer = html.rsplit(
        "memoryRetrievalAuditsEl.innerHTML =", 1
    )[1].split("memoryEpochsEl.innerHTML =", 1)[0]
    assert "retrieval_query" not in retrieval_renderer
    assert "source_summary" not in retrieval_renderer
    assert "content_json" not in retrieval_renderer


def test_control_console_renders_explainable_relationship_stage_events():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'id="memoryRelationshipStageEvents"' in html
    assert "diagnostics.relationship_stage_events || []" in html
    assert "item.previous_stage" in html
    assert "item.relationship_stage" in html
    assert "item.quality.continuity" in html
    assert "item.quality.knowledge" in html
    assert "item.quality.helpfulness" in html
    assert "item.quality.attunement" in html
    assert "item.reason_codes" in html


def test_control_console_shows_memory_detail_failures_instead_of_selection_prompt():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert "subjectMemoryError: \"\"" in html
    assert 'state.subjectMemoryError = data.message || "记忆详情加载失败";' in html
    assert 'subjectMemoryEmptyEl.textContent = `记忆可视化加载失败：${state.subjectMemoryError}`;' in html
    assert "state.subjectMemoryError = \"\";" in html


def test_control_console_exposes_all_typed_companion_memory_controls():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'id="memoryControlForm"' in html
    assert 'id="memoryControlAction"' in html
    for action in (
        "forget_evidence",
        "forget_theme",
        "correct_evidence",
        "set_boundary",
        "revoke_boundary",
        "reset_relationship",
        "purge_personal_memory",
        "confirm_candidate",
        "reject_candidate",
    ):
        assert f'<option value="{action}">' in html
    assert "async function submitMemoryControl(event)" in html
    assert "function memoryControlPayload(action)" in html
    assert "function nextMemoryControlKey(action)" in html
    assert "/api/xiaoxin/admin/memory-subjects/${encodeURIComponent(state.selectedSubjectId)}/control" in html
    assert "X-Xiaoxin-CSRF" in html
    assert "xiaoxin_csrf=" in html
    assert "confirmation," in html
    assert 'confirmation !== "RESET_RELATIONSHIP"' in html
    assert 'confirmation !== "PURGE_PERSONAL_MEMORY"' in html
    assert "await loadSubjectMemoryDetail();" in html
    assert 'data-memory-prefill-action="confirm_candidate"' in html
    assert 'data-memory-prefill-action="reject_candidate"' in html
    assert '["forget_evidence", "revoke_boundary", "confirm_candidate", "reject_candidate"]' in html
    assert (
        'item.status === "candidate" && '
        'item.source_kind === "conversation_candidate"'
    ) in html


def test_control_console_target_devices_use_all_admin_visible_devices():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert "function targetDevices()" in html
    assert "return state.devices;" in html
    assert 'device.owner_user_id && device.bind_status === "bound"' not in html
    assert "const selectableDevices = targetDevices();" in html
    assert "const selectableDevices = enabledDevices();" not in html


def test_control_console_removes_account_binding_actions_from_device_rows():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert "data-bind-device" not in html
    assert "data-unbind-device" not in html
    assert "确定解绑设备" not in html
    assert "data-wake-device" in html


def test_control_console_marks_bound_and_unbound_devices_in_list_and_picker():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert "function bindStatusBadgeClass(status)" in html
    assert "function deviceOwnerLabel(device)" in html
    assert 'bindStatusBadgeClass(device.bind_status)' in html
    assert 'bindStateLabel(device.bind_status)' in html
    assert 'deviceOwnerLabel(device)' in html
    assert "deviceOptionLabel(device)" in html
    assert "已绑定" in html
    assert "未绑定" in html
    assert "owner:" in html


def test_control_console_removes_demo_notification_editor():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'id="demoNotifications"' not in html
    assert 'id="addDemoNotificationBtn"' not in html
    assert "function addDemoNotification()" not in html
    assert "function sendDemoNotification(" not in html
    assert "data-demo-send" not in html
    assert "data-demo-remove" not in html


def test_control_console_includes_one_click_notification_showcase():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'id="showDemoNotificationsBtn"' in html
    assert 'id="demoNotificationPreview"' in html
    assert "function demoNotificationShowcase()" in html
    assert "function renderDemoNotifications()" in html
    assert "async function applyDemoNotificationShowcase()" in html
    assert "const notifications = demoData().notifications || [];" in html
    assert "demoData().notifications = demoNotificationShowcase();" in html
    assert "renderDemoNotifications();" in html
    assert "await saveDemoData();" in html
    assert "await syncDemoOverview();" in html
    assert 'document.querySelector("#showDemoNotificationsBtn").addEventListener("click", applyDemoNotificationShowcase);' in html
    assert "/api/xiaoxin/demo-data/notifications/${encodeURIComponent(notification.id)}/send" not in html


def test_control_console_removes_device_row_overview_sync_button():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'id="syncDemoOverviewBtn"' in html
    assert "function syncDemoOverview()" in html
    assert 'data-sync-overview="' not in html
    assert "function syncDeviceOverview(" not in html


def test_control_console_has_one_mqtt_overview_sync_action_per_device_row():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    device_row_template = html.split(
        'devicesEl.innerHTML = state.devices.map((device) => `', 1
    )[1].split('`).join("");', 1)[0]
    assert device_row_template.count("data-sync-overview-mqtt") == 1
    assert "async function syncDeviceOverviewMqtt(deviceId, button)" in html
    assert "/api/xiaoxin/devices/${encodeURIComponent(deviceId)}/overview-mqtt-sync" in html
    assert "button.disabled = true;" in html
    assert "button.disabled = false;" in html
    assert "button.textContent = previousLabel;" in html
    assert 'document.querySelectorAll("[data-sync-overview-mqtt]")' in html
    assert "button.dataset.syncOverviewMqtt" in html
    assert "data-sync-overview=" not in device_row_template


def test_control_console_hides_notification_fields_for_course_and_todo_events():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert '<label class="notification-field">标题' in html
    assert '<label class="notification-field">标签' in html
    assert '<label class="notification-field full">内容' in html
    assert '<label class="notification-field">有效期（毫秒）' in html
    assert '<label class="notification-field">播报' in html
    assert '<label class="notification-field full">播报文本' in html
    assert 'document.querySelectorAll(".notification-field").forEach((element) => {' in html
    assert 'element.style.display = state.event === "notification" ? "grid" : "none";' in html


def test_control_console_event_tabs_apply_matching_payload_templates():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert "function templateNameForEvent(eventName)" in html
    assert 'if (eventName === "course_reminder") {' in html
    assert 'if (eventName === "todo_reminder") {' in html
    assert "applyTemplate(templateNameForEvent(button.dataset.event));" in html


def test_control_console_includes_text_chat_test_panel():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'id="textChatInput"' in html
    assert 'id="sendTextChatBtn"' in html
    assert "async function sendTextChat()" in html
    assert 'method: "POST"' in html
    assert "const deviceId = deviceSelect.value;" in html
    assert "state.devices.find((item) => item.device_id === deviceId)" in html
    assert 'device.state !== "connected"' in html
    assert "JSON.stringify({ text })" in html
    assert 'headers: { "Content-Type": "application/json" }' in html
    assert "text.length > 500" in html
    assert "/api/xiaoxin/devices/${encodeURIComponent(deviceId)}/text-chat" in html
    assert "textChatInput.value.trim()" in html


def test_control_console_text_chat_status_messages_are_readable_chinese():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'setStatus("请选择目标设备")' in html
    assert 'setStatus("请选择在线设备")' in html
    assert 'setStatus("文本不能为空")' in html
    assert 'setStatus("文本不能超过 500 个字符")' in html
    assert 'setStatus(data.message || "发送失败")' in html
    assert 'setStatus("已发送到设备")' in html
    assert 'setStatus("发送失败")' in html


def test_control_console_collapses_empty_historical_memory_subjects():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert "function isHistoricalEmptyMemorySubject(subject)" in html
    assert 'subject.readiness?.code !== "ready"' in html
    assert "counts.available === true" in html
    assert "Number(counts.evidence || 0) === 0" in html
    assert "Number(counts.candidate_facts || 0) === 0" in html
    assert "Number(counts.jobs || 0) === 0" in html
    assert "Number(counts.errors || 0) === 0" in html
    assert '<details class="subject-history">' in html
    assert "历史空主体（${historicalSubjects.length}）" in html
