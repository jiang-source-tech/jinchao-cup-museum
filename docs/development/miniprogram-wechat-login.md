# Miniprogram WeChat Login

The miniprogram uses `wx.login` to get a temporary `code`, then posts that code to
`/api/miniprogram/session`. The server must exchange the code with WeChat
`jscode2session` to get the real `openid`.

For the HZCU Xiaoxin miniprogram, set the server environment variables before
starting the backend:

```bash
export XIAOXIN_MINIPROGRAM_APPID=wx9636b2edd0f63d53
export XIAOXIN_MINIPROGRAM_SECRET=<WeChat miniprogram AppSecret>
```

Do not commit the AppSecret to git.

When using `main/xiaozhi-server/docker-compose.yml`, set the same variables in
the shell or in a local `.env` file before recreating the service:

```bash
XIAOXIN_MINIPROGRAM_APPID=wx9636b2edd0f63d53
XIAOXIN_MINIPROGRAM_SECRET=<WeChat miniprogram AppSecret>
```

If the secret is missing, `/api/miniprogram/session` returns:

```text
wechat code exchange unavailable
```

In that state the miniprogram may fall back to development identities such as
`dev-openid-001` or `mock-openid`, and different computers will not share the
same device binding.
