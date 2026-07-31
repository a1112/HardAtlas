# 生产运行契约

HardAtlas 将缺失的生产配置视为启动错误，不以开发默认值继续运行。API 在
`HARDATLAS_ENVIRONMENT=production` 时要求：

- `HARDATLAS_AUTH_MODE=oidc`；
- PostgreSQL 数据库 URL；
- `HARDATLAS_SEARCH_BACKEND=opensearch`；
- `HARDATLAS_AUTO_CREATE_SCHEMA=false`，数据库结构由 Alembic 提前升级；
- `HARDATLAS_SEED_FIXTURE_CONTENT=false`；
- `HARDATLAS_ENABLE_HARDWARE_FIXTURE_EXTENSION=false`；
- CORS 列表中不包含 `http://localhost` 或 `http://127.0.0.1`。

若启用 `HARDATLAS_AUTO_RELEASE_ENABLED=true`，Dramatiq Worker 同样必须使用
`HARDATLAS_SEARCH_BACKEND=opensearch`、相同 PostgreSQL 和 OpenSearch alias。
`HARDATLAS_AUTO_RELEASE_BATCH_SIZE` 控制一次确定性批次的最大提案数；它不改变政策
准入、路径冲突或 Release 原子性。

发布完成后必须运行 `release.published` Outbox dispatcher 与 publication Worker。
`HARDATLAS_AUTO_ROLLBACK_ENABLED=true` 只对自动 Release 的确定性验证失败生效；
OpenSearch 暂时不可用会重试而不会触发回滚，人工 Release 始终要求显式回滚操作。

Web 与 Admin 在 `NODE_ENV=production` 时默认选择 OIDC，而不是 development
身份。两者都要求明确设置 `HARDATLAS_API_URL` 和
`HARDATLAS_OIDC_ISSUER`。公开 Web 若无法访问 API 会返回真实错误，不会把本地
catalogue 伪装成线上百科内容。

`https://tauri.localhost` 与 `tauri://localhost` 是签名桌面应用的明确来源，不属于
HTTP localhost 开发来源，可以按部署需要保留。浏览器 Web/Admin 来源应使用实际 HTTPS
域名。

模型调用另受 [`model-gateway-and-proxy.md`](model-gateway-and-proxy.md) 约束：
未配置代理时不发生外部请求，默认拒绝供应商直连地址。
