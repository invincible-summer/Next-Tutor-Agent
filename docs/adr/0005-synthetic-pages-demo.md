# ADR-0005: GitHub Pages 演示仅使用项目自制的 synthetic fixtures

- 状态：accepted
- 日期：2026-10

## Context

GitHub Pages 演示站需要一个"看起来像真实产品"的只读快照。真实用户数据（会话、画像、笔记）是隐私数据不可公开；真实教材内容受版权约束不可分发（ADR-0001）。早期演示曾依赖真实导出与固定 commit 的公共 PDF，均与 source-only 边界冲突。

## Decision

- Pages 演示的唯一内容源是 `fixtures/demo/`：全部为项目自制的合成数据（虚构书名、`fx_*` 键），并在其 README 中声明 synthetic provenance；
- 导出流程（`scripts/demo/export_pages_demo.py`）只读 synthetic corpus 生成静态只读快照；
- CI（`pages.yml`）在部署前跑 demo 契约测试与 artifact 检查（体积预算、无真实数据形态）；
- 不提供任何"真实数据脱敏后上 demo"的通道。

## Consequences

- 演示内容与真实用户/教材数据之间没有流转路径，版权与隐私边界由构建流程保证而非人工审查。
- 演示无法展示真实教材语料的效果，接受该损失。
- synthetic corpus 的维护成为演示质量的唯一变量；fixtures 契约由测试守护，防止导出器与 corpus 漂移。
