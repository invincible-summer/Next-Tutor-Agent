# compliance — 第三方依赖与许可证

生成器与证据边界见 [dependency-and-sbom.md](../../docs/compliance/dependency-and-sbom.md)。

- `generate_notices.py`：读取根 lock、各 workspace manifest、Python requirements/constraints 与安装元数据，保留原文，生成机器清单、包索引和 CycloneDX SBOM。
- 默认离线；`--fetch` 仅从官方 npm/PyPI registry 下载指定版本元数据与许可证所在发行包，不安装或执行下载代码；`--check` 离线检查输入与原文哈希、生成物一致性。
- 不产生 LLM 费用、不读取运行数据、不需要教材或用户数据；输出为可入库的第三方声明，在本地或 CI 从仓库根执行。
- `audit_historical_third_party.py`：一次性历史第三方 artifact 审计（全部 refs 扫描二进制/vendor 产物 + release asset 核对），输出 `licenses/history-audit-<date>.json`；verdict=clean 即无需改写 Git 历史。
- `check_license_policy.py`：闭源商用许可政策门禁（离线）。消费 `inventory.json`/`external-components.json` 与 `policy.json`/`decisions.json`/`obligations.json`，permissive 自动放行、conditional 须 decisions 记录、阻断 pattern 与 UNKNOWN/NOASSERTION 一律失败；接入 CI repo-hygiene 与 release workflow。
- `artifact_inventory.py`：发行物级指纹入口——release 工作流中对实际交付文件生成 SHA256SUMS、artifact 级 CycloneDX 与冻结 inventory 快照；输出目录按次生成，不随源码提交。
- 最终二进制、容器及 voice 可选环境仍需在实际目标环境生成其安装清单，保存原生依赖许可证和对应源码提供材料。
