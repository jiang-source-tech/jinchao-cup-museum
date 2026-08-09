# Xiaoxin Overview Weather Location Mini Program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a bound student inspect automatic device-network weather location and correct it with a manual province/city selection, without requesting phone location permission.

**Architecture:** Extend the existing authenticated API service with two weather-location methods and add one compact weather-location panel to the existing “我的” profile page. The server remains responsible for IP geolocation, city validation, weather lookup, and MQTT synchronization; the mini program only selects automatic/manual mode and displays sync status.

**Tech Stack:** WeChat Mini Program JavaScript/WXML/WXSS, existing `services/xiaoxinApi.js`, Node `scripts/verify.js` test harness.

## Global Constraints

- Execute in `D:\AI_Pet\小程序\Hzcu_xiaoxin_miniprogram`.
- The current checkout has uncommitted changes in `services/xiaoxinApi.js`, `scripts/verify.js`, and records-page files. Do not overwrite them.
- Before execution, use a Codex worktree starting from the working tree or first preserve those changes in a user-approved commit; never discard them.
- Do not call `wx.getLocation`, request location permission, or infer weather from the phone's network.
- Automatic mode means “use the bound hardware device's public-IP city”.
- Manual mode requires both province and city.
- Weather-location failure does not break profile, device binding, or diagnostics loading.
- Do not expose raw public IP, public-IP HMAC, MQTT credentials, openid, or provider error bodies in the weather panel.
- Keep the existing visual system and profile page; do not add a new tab or page.

---

## File Map

**Modify:**

- `services/xiaoxinApi.js` — normalize/read/update weather location.
- `pages/profile/index.js` — load and save automatic/manual weather location state.
- `pages/profile/index.wxml` — weather-location panel.
- `pages/profile/index.wxss` — panel and mode-control styling.
- `scripts/verify.js` — API flow and static UI assertions.

## Shared API Shape

GET `/api/miniprogram/weather-location` response:

```json
{
  "success": true,
  "weatherLocation": {
    "mode": "automatic",
    "province": "浙江",
    "city": "杭州",
    "locatedAt": "2026-07-10T15:00:00+08:00",
    "weatherDate": "2026-07-10",
    "weatherSummary": "杭州 · 多云",
    "weatherDetail": "今日 26～35℃",
    "syncState": "published",
    "syncRevision": 24,
    "lastError": ""
  }
}
```

PATCH request:

```json
{"mode":"automatic"}
```

or:

```json
{"mode":"manual","province":"浙江","city":"杭州"}
```

### Task 1: Weather Location API Client

**Files:**
- Modify: `services/xiaoxinApi.js`
- Modify: `scripts/verify.js`

**Interfaces:**
- Produces: `getWeatherLocation() -> Promise<WeatherLocation>`.
- Produces: `updateWeatherLocation(input) -> Promise<WeatherLocation>`.

- [ ] **Step 1: Add failing remote API flow assertions**

Extend the existing fake `wx.request` router:

```javascript
if (url === "/api/miniprogram/weather-location" && method === "GET") {
  options.success({
    statusCode: 200,
    data: {
      success: true,
      weatherLocation: {
        mode: "automatic",
        province: "浙江",
        city: "杭州",
        weatherDate: "2026-07-10",
        weatherSummary: "杭州 · 多云",
        weatherDetail: "今日 26～35℃",
        syncState: "published",
        syncRevision: 24
      }
    }
  });
  return;
}
```

Assert normalization and both PATCH shapes:

```javascript
const automatic = await api.getWeatherLocation();
assert(automatic.mode === "automatic", "weather location must expose automatic mode");
assert(automatic.city === "杭州", "weather location must expose inferred city");
const manual = await api.updateWeatherLocation({ mode: "manual", province: "上海", city: "上海" });
assert(manual.mode === "manual", "manual weather location must persist");
```

- [ ] **Step 2: Run test and confirm red**

Run: `npm test`

Expected: FAIL because the API methods are missing.

- [ ] **Step 3: Implement normalization and methods without disturbing current changes**

```javascript
function normalizeWeatherLocation(value) {
  const location = value || {};
  return {
    mode: location.mode === "manual" ? "manual" : "automatic",
    province: String(location.province || ""),
    city: String(location.city || ""),
    locatedAt: String(location.locatedAt || location.located_at || ""),
    weatherDate: String(location.weatherDate || location.weather_date || ""),
    weatherSummary: String(location.weatherSummary || location.weather_summary || ""),
    weatherDetail: String(location.weatherDetail || location.weather_detail || ""),
    syncState: String(location.syncState || location.sync_state || "unknown"),
    syncRevision: Number(location.syncRevision || location.sync_revision || 0),
    lastError: String(location.lastError || location.last_error || "")
  };
}

async function getWeatherLocation() {
  await ensureRemoteSession();
  const response = await requestJson("/api/miniprogram/weather-location");
  return clone(normalizeWeatherLocation(response.weatherLocation));
}

async function updateWeatherLocation(input) {
  await ensureRemoteSession();
  const response = await requestJson("/api/miniprogram/weather-location", {
    method: "PATCH",
    data: input || {}
  });
  return clone(normalizeWeatherLocation(response.weatherLocation));
}
```

Export both methods.

- [ ] **Step 4: Run tests**

Run: `npm test`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/xiaoxinApi.js scripts/verify.js
git commit -m "feat: add weather location api client"
```

### Task 2: Weather Location Panel State And Actions

**Files:**
- Modify: `pages/profile/index.js`
- Modify: `scripts/verify.js`

**Interfaces:**
- Consumes: `getWeatherLocation`, `updateWeatherLocation`.

- [ ] **Step 1: Add failing source assertions**

```javascript
assert(profileJs.includes("api.getWeatherLocation"), "profile must load weather location");
assert(profileJs.includes("saveWeatherLocation"), "profile must save weather location");
assert(profileJs.includes("onWeatherModeChange"), "profile must switch automatic/manual mode");
assert(!profileJs.includes("wx.getLocation"), "profile must not request phone location");
```

- [ ] **Step 2: Run test and confirm red**

Run: `npm test`

Expected: FAIL.

- [ ] **Step 3: Extend page state and loading**

Add:

```javascript
weatherLocation: null,
weatherForm: { mode: "automatic", province: "", city: "" },
isSavingWeatherLocation: false
```

Load profile, device, and weather in parallel. Weather failure is captured into a local display state and must not reject `loadProfile()`:

```javascript
const weatherPromise = api.getWeatherLocation().catch((error) => ({
  mode: "automatic",
  province: "",
  city: "",
  weatherSummary: "天气位置暂不可用",
  weatherDetail: "",
  syncState: "error",
  lastError: error && error.message ? error.message : "请求失败"
}));
```

- [ ] **Step 4: Implement mode/input/save handlers**

```javascript
onWeatherModeChange(event) {
  this.setData({ "weatherForm.mode": event.detail.value });
},

onWeatherFieldInput(event) {
  const field = event.currentTarget.dataset.field;
  this.setData({ [`weatherForm.${field}`]: event.detail.value });
},

async saveWeatherLocation() {
  if (this.data.isSavingWeatherLocation) return;
  const form = this.data.weatherForm || {};
  if (form.mode === "manual" && (!String(form.province || "").trim() || !String(form.city || "").trim())) {
    this.showToast("请输入省份和城市", "none");
    return;
  }
  this.setData({ isSavingWeatherLocation: true });
  try {
    const input = form.mode === "manual"
      ? { mode: "manual", province: String(form.province).trim(), city: String(form.city).trim() }
      : { mode: "automatic" };
    const weatherLocation = await api.updateWeatherLocation(input);
    this.setData({ weatherLocation, weatherForm: Object.assign({}, weatherLocation) });
    this.showToast("天气位置已更新");
  } finally {
    this.setData({ isSavingWeatherLocation: false });
  }
}
```

- [ ] **Step 5: Run tests**

Run: `npm test`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pages/profile/index.js scripts/verify.js
git commit -m "feat: manage overview weather location"
```

### Task 3: Weather Location Profile UI

**Files:**
- Modify: `pages/profile/index.wxml`
- Modify: `pages/profile/index.wxss`
- Modify: `scripts/verify.js`

- [ ] **Step 1: Add failing static markup assertions**

```javascript
assert(profileWxml.includes("天气位置"), "profile must render weather location panel");
assert(profileWxml.includes('value="automatic"'), "weather panel must expose automatic mode");
assert(profileWxml.includes('value="manual"'), "weather panel must expose manual mode");
assert(profileWxml.includes('bindtap="saveWeatherLocation"'), "weather panel must save changes");
assert(profileWxml.includes("weatherLocation.weatherSummary"), "weather panel must show current daily weather");
```

- [ ] **Step 2: Run test and confirm red**

Run: `npm test`

Expected: FAIL.

- [ ] **Step 3: Add one panel between device and diagnostics**

```xml
<view class="panel weather-panel" wx:if="{{weatherLocation}}">
  <view class="panel-heading">
    <view class="heading-stack">
      <text>天气位置</text>
      <text class="heading-caption">用于同步硬件总览页当天预报</text>
    </view>
    <text class="state-chip">{{weatherLocation.syncState === 'published' ? '已同步' : '待同步'}}</text>
  </view>

  <text class="weather-summary">{{weatherLocation.weatherSummary || '天气位置未知'}}</text>
  <text class="profile-line">{{weatherLocation.weatherDetail}}</text>

  <radio-group class="weather-mode-group" bindchange="onWeatherModeChange">
    <label><radio value="automatic" checked="{{weatherForm.mode === 'automatic'}}" />根据设备网络自动定位</label>
    <label><radio value="manual" checked="{{weatherForm.mode === 'manual'}}" />手动指定省市</label>
  </radio-group>

  <view class="weather-city-grid" wx:if="{{weatherForm.mode === 'manual'}}">
    <input value="{{weatherForm.province}}" data-field="province" bindinput="onWeatherFieldInput" placeholder="省份，例如浙江" />
    <input value="{{weatherForm.city}}" data-field="city" bindinput="onWeatherFieldInput" placeholder="城市，例如杭州" />
  </view>

  <button class="secondary-button" loading="{{isSavingWeatherLocation}}" disabled="{{isSavingWeatherLocation}}" bindtap="saveWeatherLocation">保存天气位置</button>
</view>
```

- [ ] **Step 4: Add scoped styling**

Reuse existing panel, input, button, and state-chip tokens. Add only `.weather-panel`, `.weather-summary`, `.weather-mode-group`, and `.weather-city-grid`; keep the two-column grid collapsing under the existing 420px media query.

- [ ] **Step 5: Run tests**

Run: `npm test`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pages/profile/index.wxml pages/profile/index.wxss scripts/verify.js
git commit -m "feat: add weather location settings panel"
```

### Task 4: Mini Program Verification

- [ ] **Step 1: Run the full verification harness**

Run: `npm test`

Expected: all assertions pass.

- [ ] **Step 2: Inspect the working tree for overlap with pre-existing changes**

Run: `git status --short && git diff --check`

Expected: no whitespace errors; only the planned profile/API/test changes plus explicitly preserved prior user changes.

- [ ] **Step 3: WeChat Developer Tools manual test**

Verify automatic mode displays inferred province/city and weather, manual mode requires province/city, save updates the displayed sync state, refusal/server errors do not break profile/device/diagnostics, and no location permission prompt appears.

- [ ] **Step 4: Commit any verification-only copy fixes**

If manual QA required copy/style changes, commit only those files:

```bash
git add pages/profile/index.js pages/profile/index.wxml pages/profile/index.wxss services/xiaoxinApi.js scripts/verify.js
git commit -m "fix: polish weather location settings"
```
