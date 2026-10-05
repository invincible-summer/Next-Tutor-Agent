import type { MessageDict } from "@next-tutor/i18n";

/**
 * 域字典注册表：每个 feature 的字典文件在模块加载时 push 进来，
 * 使各里程碑可以新增独立字典文件而不改共享 index（并行开发不冲突）。
 */
export const zhDicts: MessageDict[] = [];
export const enDicts: MessageDict[] = [];

export function registerDicts(zhDict: MessageDict, enDict: MessageDict): void {
  zhDicts.push(zhDict);
  enDicts.push(enDict);
}
