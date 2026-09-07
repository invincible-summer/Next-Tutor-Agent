import type { AttachmentMeta } from "@/lib/types";

/** 资源页文件模型：资料库文件或会话附件（本地扩展，不改共享类型）。 */
export interface ResourceFile extends AttachmentMeta {
  summary?: string;
  topics?: string[];
  has_original?: boolean;
}

/** 左栏来源引用：资料库视图（全部/未归档/文件夹）或会话附件。 */
export type SourceRef =
  | { kind: "all" } // 全部文件
  | { kind: "root" } // 未归档
  | { kind: "folder"; id: string } // 指定文件夹（含工作区专属夹）
  | { kind: "session"; id: string }; // 会话附件（只读）
