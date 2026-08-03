# Upstream 登录态同步扩展

本地 Chrome Manifest V3 扩展，仅在 Upstream 添加渠道或手动点击同步时读取指定站点的登录态。

## 安装

1. 打开 `chrome://extensions` 并启用开发者模式。
2. 点击“加载已解压的扩展程序”。
3. 选择本目录 `extensions/upstream-session-bridge`。

扩展加载时一次性声明 HTTP/HTTPS 上游站点访问权限，避免同步过程中因异步调用丢失 Chrome 用户手势而无法申请权限。sub2api 适配器只读取 Local Storage 中的 `auth_token`、`refresh_token` 和 `token_expires_at`，不读取密码或 Cookie。NewAPI 优先使用 access token/session；兼容旧版 NewAPI 时读取 `uid`，先在目标页面验证 `/api/user/self` 和分组接口，再通过 Cookie 权限读取目标 Origin 的 Cookie 组装请求头。

扩展只连接 `localhost`、`127.0.0.1` 上的 Upstream 完成接口。AT/RT 由扩展直接提交后端，不返回控制台页面、不写扩展存储、不输出日志。
