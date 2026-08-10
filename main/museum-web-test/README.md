# museum-web-test

金潮杯博物馆项目的浏览器语音链路调试工具。它只用于开发和协议联调，不是比赛设备的主运行时。

## 启动

```bash
pip install -r wakeword_runtime/requirements.txt
python start.py
```

启动后访问：

- 页面：`http://127.0.0.1:8006/index.html`
- 唤醒事件桥：`ws://127.0.0.1:8006/wakeword-ws`
- 健康检查：`http://127.0.0.1:8006/health`

默认唤醒词为“你好讲解员”和“你好博物馆”。设备真实唤醒模型、服务端 WebSocket 和 OTA 配置仍以各自仓库中的当前合同为准。
