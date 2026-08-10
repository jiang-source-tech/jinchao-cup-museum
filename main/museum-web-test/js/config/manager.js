// 配置管理模块

// 默认唤醒词列表
export const DEFAULT_WAKE_WORDS = '你好讲解员\n你好博物馆';

// 生成随机MAC地址
function generateRandomMac() {
    const hexDigits = '0123456789ABCDEF';
    let mac = '';
    for (let i = 0; i < 6; i++) {
        if (i > 0) mac += ':';
        for (let j = 0; j < 2; j++) {
            mac += hexDigits.charAt(Math.floor(Math.random() * 16));
        }
    }
    return mac;
}

// 加载配置
export function loadConfig() {
    const deviceMacInput = document.getElementById('deviceMac');
    const deviceNameInput = document.getElementById('deviceName');
    const clientIdInput = document.getElementById('clientId');
    const otaUrlInput = document.getElementById('otaUrl');
    const wakewordWsUrlInput = document.getElementById('wakewordWsUrl');
    const wakewordEnabledInput = document.getElementById('wakewordEnabled');
    const wakewordListInput = document.getElementById('wakewordList');

    // 从localStorage加载MAC地址，如果没有则生成新的
    let savedMac = localStorage.getItem('museum_web_test_deviceMac');
    if (!savedMac) {
        savedMac = generateRandomMac();
        localStorage.setItem('museum_web_test_deviceMac', savedMac);
    }
    deviceMacInput.value = savedMac;

    // 从localStorage加载其他配置
    const savedDeviceName = localStorage.getItem('museum_web_test_deviceName');
    if (savedDeviceName) {
        deviceNameInput.value = savedDeviceName;
    }

    const savedClientId = localStorage.getItem('museum_web_test_clientId');
    if (savedClientId) {
        clientIdInput.value = savedClientId;
    }

    const savedOtaUrl = localStorage.getItem('museum_web_test_otaUrl');
    if (savedOtaUrl) {
        otaUrlInput.value = savedOtaUrl;
    }

    const savedWakewordWsUrl = localStorage.getItem('museum_web_test_wakewordWsUrl');
    if (savedWakewordWsUrl !== null && wakewordWsUrlInput) {
        wakewordWsUrlInput.value = savedWakewordWsUrl;
    }

    const savedWakewordEnabled = localStorage.getItem('museum_web_test_wakewordEnabled');
    if (savedWakewordEnabled !== null && wakewordEnabledInput) {
        wakewordEnabledInput.value = savedWakewordEnabled;
    }

    const savedWakewordList = localStorage.getItem('museum_web_test_wakewordList');
    if (savedWakewordList !== null && wakewordListInput) {
        wakewordListInput.value = savedWakewordList;
    } else if (wakewordListInput) {
        wakewordListInput.value = DEFAULT_WAKE_WORDS;
    }

    const emojiEnabledInput = document.getElementById('emojiEnabled');
    const savedEmojiEnabled = localStorage.getItem('museum_web_test_emojiEnabled');
    if (savedEmojiEnabled !== null && emojiEnabledInput) {
        emojiEnabledInput.value = savedEmojiEnabled;
    }
}

// 保存配置
export function saveConfig() {
    const deviceMacInput = document.getElementById('deviceMac');
    const deviceNameInput = document.getElementById('deviceName');
    const clientIdInput = document.getElementById('clientId');
    const wakewordWsUrlInput = document.getElementById('wakewordWsUrl');
    const wakewordEnabledInput = document.getElementById('wakewordEnabled');
    const wakewordListInput = document.getElementById('wakewordList');

    localStorage.setItem('museum_web_test_deviceMac', deviceMacInput.value);
    localStorage.setItem('museum_web_test_deviceName', deviceNameInput.value);
    localStorage.setItem('museum_web_test_clientId', clientIdInput.value);
    const emojiEnabledInput = document.getElementById('emojiEnabled');
    if (emojiEnabledInput) {
        localStorage.setItem('museum_web_test_emojiEnabled', emojiEnabledInput.value);
    }
    if (wakewordEnabledInput) {
        localStorage.setItem('museum_web_test_wakewordEnabled', wakewordEnabledInput.value);
    }
    if (wakewordListInput) {
        localStorage.setItem('museum_web_test_wakewordList', wakewordListInput.value);
    }
    if (wakewordWsUrlInput && wakewordWsUrlInput.value.trim()) {
        localStorage.setItem('museum_web_test_wakewordWsUrl', wakewordWsUrlInput.value.trim());
    }
}

// 获取配置值
export function getConfig() {
    // 从DOM获取值
    const deviceMac = document.getElementById('deviceMac')?.value.trim() || '';
    const deviceName = document.getElementById('deviceName')?.value.trim() || '';
    const clientId = document.getElementById('clientId')?.value.trim() || '';
    const emojiEnabled = document.getElementById('emojiEnabled')?.value !== 'false';

    return {
        deviceId: deviceMac,  // 使用MAC地址作为deviceId
        deviceName,
        deviceMac,
        clientId,
        emojiEnabled
    };
}

// 保存连接URL
export function saveConnectionUrls() {
    const otaUrl = document.getElementById('otaUrl').value.trim();
    const wsUrl = document.getElementById('serverUrl').value.trim();
    localStorage.setItem('museum_web_test_otaUrl', otaUrl);
    localStorage.setItem('museum_web_test_wsUrl', wsUrl);
}
