# OIDC、最小权限与不可变审计

## API 身份边界

公开知识空间、分类、搜索和已发布条目允许匿名读取。Agent 运行、提案、来源注册、
审核、发布、回滚与审计读取必须经过身份验证。

生产模式使用 OIDC access token：

- 验证签名算法 allow-list、JWKS 签名、`iss`、`aud`、`sub`、`iat` 和 `exp`；
- 从直接、realm 或 audience resource claims 提取角色；
- 从可配置 workspace claim 提取组织空间；缺省时由 issuer 与 subject 稳定派生个人空间；
- 角色映射到细粒度权限，而不是仅判断“已登录”；
- `development` Header 身份在生产环境启动时被硬性拒绝。

当前角色包括 `agent-runner`、`editor`、`policy-evaluator`、`reviewer`、
`publisher`、`source-admin`、`auditor` 和全权限 `admin`。审核记录中的 reviewer
始终取自已认证主体，客户端不能通过请求体伪造审核者。

## Admin 的安全会话

Admin 使用 Next.js BFF，而不是把 access token 暴露给浏览器脚本：

1. `/api/auth/login` 生成随机 state 和 PKCE verifier/challenge；
2. OIDC Authorization Code 回调验证 state 并交换 token；
3. access/refresh token 只存入 `HttpOnly`、`SameSite=Lax`、生产 `Secure` Cookie；
4. `/api/backend/*` 服务端代理添加 Bearer token；
5. access token 过期后由服务端使用 refresh token 刷新并轮换 Cookie；
6. 浏览器端组件只能读取 `/api/auth/session` 的布尔会话状态，不能读取 token。

本地开发 BFF 注入显式开发主体与角色；这些 Header 只在 API 的 development 模式生效。
个人收藏的 workspace 作用域和 PostgreSQL RLS 约束见
[`personal-collections-and-rls.md`](personal-collections-and-rls.md)。

## 审计哈希链

每次成功或被拒绝的特权操作记录：

- OIDC/development 主体、角色和认证方式；
- action、resource type/id、outcome；
- request ID 和非敏感元数据；
- 前一事件哈希和当前事件 SHA-256。

审计事件只允许追加。`GET /api/v1/audit-events/status` 会重新计算整条哈希链；
任一历史文档或链指针发生变化都会返回失败。管理后台 `/audit` 展示链状态和最近事件。

生产 PostgreSQL 应将审计表写权限限制给 API service role，并把审计事件异步复制到独立
WORM/合规存储，以抵御数据库超级用户级别的修改。
