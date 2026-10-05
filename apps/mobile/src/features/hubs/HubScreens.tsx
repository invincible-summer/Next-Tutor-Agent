import React from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import {
  BookOpen,
  CalendarDays,
  ClipboardCheck,
  FileText,
  GitBranch,
  Image,
  Library,
  NotebookPen,
  Sparkles,
} from "lucide-react-native";
import { useCopy } from "@/lib/copy";
import { useTheme } from "@/ui";
import { Body, Hint, Tile, Section } from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
import { useAdaptive } from "@/shell/adaptive/window-class";
import { domainRoute, type DomainTarget } from "@/shell/routes";
type Entry = {
  kind: DomainTarget["kind"];
  title: string;
  subtitle: string;
  icon: typeof BookOpen;
};
export function Hub({ kind }: { kind: "learn" | "library" | "tools" }) {
  const c = useCopy();
  const router = useRouter();
  const { theme } = useTheme();
  const adaptive = useAdaptive();
  const entries: Entry[] =
    kind === "learn"
      ? [
          {
            kind: "lesson",
            title: c("课堂", "Courses"),
            subtitle: c(
              "课件、讲稿与互动练习，按自己的节奏学习。",
              "Slides, narration and practice, at your own pace.",
            ),
            icon: BookOpen,
          },
          {
            kind: "assessment",
            title: c("测评练习", "Assessment"),
            subtitle: c(
              "一次一个问题，让下一步更清晰。",
              "One question at a time, for a clearer next step.",
            ),
            icon: ClipboardCheck,
          },
          {
            kind: "plan",
            title: c("计划与编排", "Learning plan"),
            subtitle: c(
              "把目标变成今天可以做的一小步。",
              "Turn your goals into a small step today.",
            ),
            icon: CalendarDays,
          },
        ]
      : kind === "tools"
        ? [
            {
              kind: "illustration",
              title: c("情景配图", "Scenario illustration"),
              subtitle: c(
                "用对话创作可缩放的知识图示。",
                "Create a zoomable illustration through conversation.",
              ),
              icon: Sparkles,
            },
            {
              kind: "diagrams",
              title: c("图示素材", "Diagram library"),
              subtitle: c(
                "搜索素材、调整参数，保存你的版本。",
                "Find materials, adjust parameters and save your version.",
              ),
              icon: Image,
            },
          ]
        : [
            {
              kind: "resources",
              title: c("教材与文件", "Materials"),
              subtitle: c(
                "整理、上传与选择学习资料。",
                "Organize, upload and select learning sources.",
              ),
              icon: Library,
            },
            {
              kind: "note",
              title: c("笔记", "Notes"),
              subtitle: c(
                "把零散的想法连成自己的理解。",
                "Connect thoughts into your own understanding.",
              ),
              icon: NotebookPen,
            },
            {
              kind: "concept",
              title: c("知识图谱", "Knowledge"),
              subtitle: c(
                "看看知识之间如何连接。",
                "Explore how concepts connect.",
              ),
              icon: GitBranch,
            },
            {
              kind: "diagrams",
              title: c("图示库", "Diagrams"),
              subtitle: c(
                "让抽象的概念变得可见。",
                "Make abstract concepts visible.",
              ),
              icon: Image,
            },
            {
              kind: "illustration",
              title: c("创作工具", "Creative tools"),
              subtitle: c(
                "从一句描述开始，创作情景配图。",
                "Turn a description into an illustration.",
              ),
              icon: Sparkles,
            },
          ];
  const titles = {
    learn: c("学习，一步一步来", "Make room to learn"),
    library: c("你的知识收藏", "Your collection of ideas"),
    tools: c("让想法看得见", "Make an idea visible"),
  };
  return (
    <FeatureShell
      title={titles[kind]}
      subtitle={c(
        "留住理解，让好奇继续。",
        "Keep understanding. Stay curious.",
      )}
      back={kind === "tools"}
      auth={false}
    >
      <Body>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 16 }}>
          {entries.map(({ kind: k, title, subtitle, icon: Icon }) => (
            <Tile
              key={k}
              title={title}
              subtitle={subtitle}
              icon={<Icon size={24} color={theme.colors.accent} />}
              onPress={() =>
                router.push(domainRoute({ kind: k } as DomainTarget))
              }
              style={{
                width: adaptive.maxPanes > 1 ? "31.5%" : "100%",
                flexGrow: 1,
              }}
            />
          ))}
        </View>
        <Hint>
          {c(
            "学习记录与生成结果来自你的服务端。",
            "Learning records and generated content come from your server.",
          )}
        </Hint>
      </Body>
    </FeatureShell>
  );
}
export function LearningHubScreen() {
  return <Hub kind="learn" />;
}
export function LibraryHubScreen() {
  return <Hub kind="library" />;
}
export function ToolsHubScreen() {
  return <Hub kind="tools" />;
}
