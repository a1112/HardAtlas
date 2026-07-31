# 模型网关与代理边界

HardAtlas 的 Agent 不直接连接模型供应商。所有可选模型调用都经过
`hardatlas_ai.OpenAICompatibleModelGateway`，其唯一目标地址来自
`HARDATLAS_MODEL_GATEWAY_URL`。项目没有供应商默认 URL，因此未配置网关时不会产生
外部模型请求。

## 配置

```dotenv
HARDATLAS_MODEL_GATEWAY_URL=https://model-gateway.example.internal
HARDATLAS_MODEL_GATEWAY_MODEL=approved-model-alias
HARDATLAS_MODEL_GATEWAY_ENDPOINT_PATH=/v1/responses
HARDATLAS_MODEL_GATEWAY_ID=production-gateway
HARDATLAS_MODEL_ALLOW_DIRECT_PROVIDER=false
```

`HARDATLAS_MODEL_GATEWAY_API_KEY` 只能由 Shell 或 Secret Manager 注入，不能写入仓库。
默认拒绝 `api.openai.com` 和 Azure OpenAI 直连地址；只有显式设置
`HARDATLAS_MODEL_ALLOW_DIRECT_PROVIDER=true` 才能解除该保护。

## 执行语义

- `deterministic` Agent 被 Runner 禁止报告模型调用。
- `optional-model` Agent 在网关未配置或模型所需上下文不足时确定性执行，并在节点输出
  中记录 `executionMode` 或 `resolutionMode`。
- 网关已配置且输入足够时，Agent 使用严格 JSON Schema 请求代理；代理失败会让节点失败
  并进入既有重试流程，不会静默伪装成确定性成功。
- `model-required` Agent 若没有模型调用会被 Runner 拒绝。

每次成功调用会把网关 ID、无凭据的代理基址、provider、model、request ID、Agent ID、
Token 与成本写入 `GraphRun.modelInvocations` 和节点检查点。API Key、Authorization
Header 和原始代理响应不会进入运行记录。

失败调用同样计入 `modelCalls` 并记录 `status=failed`、request ID（若代理已返回）和
稳定 `errorCode`。运行记录不保存错误响应正文，失败重试因此不能绕过模型调用预算，也
不会把供应商响应中的敏感内容复制到检查点。

内容抽取只允许模型基于 `sourceExcerpt` 形成候选；实体消歧只允许从
`candidateEntityIds` 白名单中选择。模型结果仍只是受治理提案输入，不能直接修改已发布
百科条目。

公开知识问答复用相同代理边界，但具有独立的默认关闭开关
`HARDATLAS_PUBLIC_ANSWER_MODEL_ENABLED`。浏览器只调用 Web 同源 BFF；API 先从当前
发布修订构造引用完整的证据白名单，模型只能返回该白名单内的 evidence ID。未知或重复
ID 会拒绝模型输出并显式降级为确定性检索摘要。回答记录只公开 gateway ID、模型别名、
request ID、状态和用量，不公开代理基址或凭据。详见
[`evidence-grounded-answering.md`](evidence-grounded-answering.md)。

来源注册的 `modelProcessingPolicy` 默认为 `forbidden`。只有来源整体采集策略通过且
该字段为 `approved-gateway` 时，API/Worker 才会在可信调度输入中写入
`modelProcessingAllowed=true`。客户端自行提交同名字段会被服务端覆盖，未注册来源、
暂停来源或许可未通过的来源只能走确定性路径。
