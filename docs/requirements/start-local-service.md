# 小芯需求工作台本地服务启动说明

小芯需求工作台由三个文件组成：

```text
docs/requirements/requirements.yaml
docs/requirements/requirements.html
docs/requirements/server.py
```

`requirements.yaml` 是唯一事实源。HTML 页面只负责渲染，`server.py` 负责校验 YAML 并提供本地 JSON 接口。

## 启动方式

在项目根目录执行：

```powershell
Set-Location D:\AI_Pet\xiaoxin-esp32-server\docs\requirements
python server.py --port 8080
```

浏览器打开：

```text
http://127.0.0.1:8080
```

## 常用接口

```text
GET /requirements.html
GET /requirements.json
GET /requirements.yaml
```

如果修改了 `requirements.yaml`，刷新浏览器即可看到新状态。

## 校验命令

在项目根目录执行：

```powershell
python -c "import sys; sys.path.insert(0, 'docs/requirements'); import server; r = server.load_requirements(); print(r['ok']); print(len(r.get('errors', []))); print(len(r['data']['items']) if r.get('data') else 0); print(r.get('errors', []))"
```

期望结果：

```text
True
0
至少 12
[]
```

如果第一行不是 `True`，先修复 YAML 结构，不要改 HTML。
