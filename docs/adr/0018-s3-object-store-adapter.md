# ADR-0018: S3 兼容 ObjectStore 适配器（boto3）

- 状态：accepted
- 日期：2026-10-06
- 关联：ADR-0010（企业持久化栈）、ADR-0017（域文档仓储——对象字节不进 JSONB 的边界由本决策承接）、ADR-0014（单实例约束解除的前提之一）。

## Context

ADR-0017 明确了边界：九域事实进 PostgreSQL JSONB，对象字节（上传原件、OCR 产物、BM25 序列化索引、插画 SVG/PNG、课件导出、TTS 音频缓存、头像）走 ObjectStore 协议。此前该协议只有 `LocalObjectStore`（数据根内 `object_store/` 目录）——单实例可用，但多实例部署要求字节层共享存储，且自建共享卷对多数部署并不现实。此前 azure/s3 是 remote.py 里会 loud 失败的保留槽位。

约束：闭源商用、零许可成本——依赖必须全部 permissive（仓库许可策略 gate 强制）；必须兼容私有部署（数据不出本机可选）与云托管两类形态；`ObjectStore` 协议（put/get/exists/size/delete，`StoredObject` 携带 sha256/content_type）不变，消费方无感切换。

## Decision

**S3 兼容适配器** `app/persistence/object_store/s3.py`：boto3（Apache-2.0）客户端，`OBJECT_STORE_BACKEND=s3` 启用；本地后端保持默认。要点：

- **线程隔离**：boto3 是同步库，所有调用经 `asyncio.to_thread`，事件循环不阻塞。
- **实现无关的完整性**：sha256 hex 存对象元数据 `sha256`（一切 S3 兼容服务都支持元数据），`get` 重算比对，不匹配抛 `ObjectIntegrityError` 拒绝返回被篡改字节。刻意不用 AWS 专有的 `ChecksumSHA256` 请求参数（MinIO/Garage 等兼容实现行为不一）。
- **SSE-S3 默认开启**（`AES256`，服务端管理密钥，无 KMS 依赖）；`OBJECT_STORE_S3_SSE=off` 关闭以兼容不支持 SSE 的端点。SSE-KMS 不引入（密钥管理是部署级决策，暂无需求）。
- **大对象 multipart**：超过阈值（16 MiB）走 transfer manager（分片上传、失败自动 abort），元数据/SSE 随 initiate 传递。
- **重试/超时**：botocore standard retry mode（≤5 次、指数退避）+ 显式 connect/read 超时（默认 30s）。
- **配置即代码**：`build_s3_store()` 读 `OBJECT_STORE_ENDPOINT/BUCKET/ACCESS_KEY/SECRET_KEY[/REGION/S3_SSE/TIMEOUT_SECONDS/S3_FORCE_PATH_STYLE]`；缺失即 startup loud 失败并点名缺失变量——绝不静默回落到其它存储层（数据绝不能落错层）。非 AWS 端点默认 path-style 寻址（自签名/内网域名友好）。
- **测试形态**：moto（Apache-2.0）进程内拦截 boto3 默认端点做集成测试（roundtrip/SSE/ContentType/元数据校验/篡改拒绝/multipart 通道/工厂配置校验/azure 槽位仍 loud）；不进生产 compose，也不依赖任何云凭据——本仓库无法完成的"云凭据实连"如实列为部署侧验收项。
- Azure Blob 维持保留槽位（loud 失败）；无需求不实现。

## Consequences

- 多实例部署的字节层前提补齐：PG 事实源（ADR-0017）+ ObjectStore 共享字节 + 派生索引共享卷 ⇒ `WEB_CONCURRENCY=1` 可解除（部署文档列出完整前提）。
- boto3 进入生产依赖（企业持久化 lane，惰性导入——local/file 模式不加载）；moto 仅测试依赖。许可证据入库：boto3/botocore/s3transfer Apache-2.0、jmespath/urllib3 MIT，全部自动允许、零策略豁免。
- ObjectStore 协议未变；LocalObjectStore 仍是默认与回退路径，`OBJECT_STORE_BACKEND` 单开关切换，误配即 loud 失败。
- 修复了暴露的潜伏缺陷：`default_store()` local 分支缺惰性导入 `LocalObjectStore`（此前无调用方，NameError 未现形）。
