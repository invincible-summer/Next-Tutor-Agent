# scripts/chem_lab — 化学实验台内容工具

仅依赖标准库 + 仓库内 `services/api`（pydantic）。三个入口：

```bash
# 校验内容目录（schema、manifest 哈希、DSL、交叉引用）；
# --write-manifest 重建 manifest.json 并刷新 content/packs/*.json 运行时快照
# （Catalog.build_pack 的输出，TS 领域引擎的向量一致性测试直接消费它）
services/api/.venv/bin/python scripts/chem_lab/validate_pack.py [--write-manifest]

# 回放全部 replay vectors 并核对 state hash；--write 用当前引擎重写向量中的哈希
services/api/.venv/bin/python scripts/chem_lab/replay_pack.py [--experiment chem.dilution] [--write]

# 文本预览某个向量的渲染帧与事件流（内容作者自查用）
services/api/.venv/bin/python scripts/chem_lab/render_preview.py content/vectors/chem.dilution/guided_happy.json
```

内容改动流程：改 `services/api/app/chem_lab/content/**` → `validate_pack.py --write-manifest`
→ `replay_pack.py --write`（若引擎语义变化）→ `replay_pack.py` 全绿后提交。
