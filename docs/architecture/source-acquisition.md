# 受治理来源采集与不可变快照

## 安全边界

Agent 不能直接把网络内容写入百科。每个来源先注册为版本化 `SourceDefinition`，
并通过机器可执行的采集策略：

- 来源必须处于 `active`；
- 许可状态必须为 `allowed`；
- 网站必须声明 `respect` robots 策略，且 robots 审核状态为 `allowed`；
- API 必须声明 `explicit-api`；
- 模型处理独立且默认禁止；只有 `approved-gateway` 且其余来源策略全部通过时，来源摘录
  才能进入批准的模型代理；
- 目标 URL 只能使用 HTTP(S)，域名必须位于显式 allow-list；
- IP 字面量不能指向私网、回环或 link-local 地址；
- 响应不能自动跟随重定向，重定向目标需重新做策略判断；
- HTTP 状态、媒体类型和响应字节数必须符合来源定义。
- `entityTypeIds`、`taxonomyNodeIds` 和 `locales` 可声明来源适用范围；空列表表示
  通用范围，非空范围必须与待维护条目的固定修订匹配。

生产网络还应使用 egress proxy 和 DNS 解析策略抵御 DNS rebinding；应用层 allow-list
不是唯一的网络隔离层。

## 快照与证据

采集成功后按原始字节计算 SHA-256，生成 `SourceSnapshot`：

- `storageKey` 为 `sources/{sourceId}/{sha256}`；
- 快照记录来源版本、许可、URL、媒体类型、字节数、ETag 与 Last-Modified；
- 快照元数据在 PostgreSQL 中不可变；
- 原始字节由 worker 写入 S3/MinIO，并附带相同 SHA-256 与许可元数据；
- 后续提取、证据核验和提案只引用快照 ID 与定位信息，不能引用会漂移的在线页面作为唯一证据。

## 运行组件

- `GET/POST /api/v1/sources`：来源注册表及实时政策判断；
- `GET /api/v1/sources/{id}/snapshots`：不可变采集历史；
- `GET/POST /api/v1/sources/{id}/acquisitions`：读取或创建持久化采集任务；
- `GET /api/v1/sources/{id}/extractions`：按来源读取结构化解析批次；
- `GET /api/v1/extractions/{batchId}/candidates`：读取带定位器与引用的字段候选；
- `GET /api/v1/sources/{id}/pipeline`：从持久化采集、快照、解析候选与 Agent
  调度记录生成端到端维护链路；
- `POST .../replay`：只对失败采集、已有快照、已有解析批次或失败 Agent 调度重建
  Outbox 事件，不跳过来源政策、版本钉住或治理关卡；
- 管理后台 `/sources`：默认以暂停、许可与 robots 待审核状态注册；
- `hardatlas-scheduler`：读取已通过政策的来源，将每个到期的五字段 cron occurrence
  转换为一个持久化采集任务；
- `acquire_source` Dramatiq actor：策略检查、HTTP 获取、S3 写入和快照元数据保存；
- `agent-source-acquisition-router@1.0.0`：对来源采集 Work Item 做确定性来源选择，
  创建带 `maintenanceWorkItemId` 的幂等采集任务；没有唯一合规来源时转为可见的
  `blocked`，不执行猜测式抓取；
- 固定解析器的来源会通过 `source.snapshot.captured` Outbox 进入
  `extract_source_snapshot_task`，未固定解析器的来源只归档快照。

采集请求不会由 API 进程直接调用 Worker。API 在同一事务中写入
`source_acquisition_job` 与 `source.acquisition.requested` outbox 事件，
Worker 确认 Redis 入队后才标记 outbox 已发布。任务状态按
`queued → dispatched → running → completed/failed` 推进；重试复用固定的
来源版本与幂等键。许可或 robots 未通过的来源无法创建采集任务。

快照元数据与 `source.snapshot.captured` 事件在同一事务中写入。即使 S3 保存成功后
Redis 短暂不可用，Dispatcher 仍会在下次轮询重新派发，不会留下无法继续处理的孤立
快照。显式重放会重新激活原聚合资源的 Outbox 事件，处理函数仍按确定性 ID 和幂等键
执行，因此交付语义是至少一次而非假定恰好一次。

- `S3SnapshotStore`：兼容 AWS S3 与 MinIO endpoint。

来源版本与快照不可覆盖。包括模型处理策略在内的配置变化需要新版本，内容变化会产生
新的哈希快照。允许采集不等于允许模型处理：`forbidden` 来源仍可走确定性解析和治理
流程。

当采集任务由维护 Work Item 触发时，任务身份固定
`workItem + entityRevision + sourceId + sourceVersion`。采集成功后，Worker
只用数据库中存在且与任务匹配的 `SourceSnapshot` 和
`SourceAcquisitionJob` 完成 Work Item；租约令牌不进入 Outbox 或审计记录。
这一步仅关闭“证据已采集”工作，不创建正文变更。后续解析候选仍必须进入受治理提案、
政策审核和版本发布链路。

## 自动调度与幂等

来源注册时会验证 `schedule` 是严格的五字段 cron。Scheduler 每次 tick 计算每个来源
最近一次应执行时间，并生成：

```text
source-schedule:{sourceId}:{sourceVersion}:{UTC occurrence}
```

作为幂等键。相同 occurrence 无论进程重启、重复 tick 或多副本竞争，都只能创建一个
`SourceAcquisitionJob`。任务与 `source.acquisition.requested` Outbox 在同一事务中
写入，之后沿用 Dispatcher → Redis/Dramatiq → Worker 链路。调度时间早于来源版本
创建时间时不会补采，暂停、许可阻断或 robots 未批准的来源也不会产生任务。
