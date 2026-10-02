// /course Hub 页面词条（zh/en）。课程卡/状态 chip/继续上课/功能未开放等词条
// 复用课堂列表页词典（展开合并，保持两处文案一致）；本文件只定义 Hub 专属的
// course.* 词条。nav.course、ws.classroom.*、ws.empty、ws.create 走全局词典。
import type { PageStrings } from "@/lib/i18n-page";
import { STRINGS as CLASSROOM_LIST_STRINGS } from "@/app/(workspace)/workspaces/[workspaceId]/classroom/strings";

export const STRINGS: PageStrings = {
  zh: {
    ...CLASSROOM_LIST_STRINGS.zh,
    "course.eyebrow": "你的课程创作空间",
    "course.art.title": "让知识，\n循序展开。",
    "course.newSpace": "新建工作学习区",
    "course.stat.spaces": "工作学习区",
    "course.stat.lessons": "课程",
    "course.stat.progress": "正在准备",
    "course.workflow": "准备课件 · 编辑讲稿 · 开始上课",
    "course.library": "我的课程",
    "course.library.hint": "按工作学习区整理，保持每一次学习的连续。",
    "course.search": "查找工作学习区",
    "course.search.empty": "没有找到这个工作学习区",
    "course.subtitle": "从一份教材，到一堂好课。让课件、讲稿与声音，在这里准备就绪。",
    "course.resume.title": "继续学习",
    "course.group.lessons": "%n 个课程",
    "course.group.viewAll": "查看全部",
    "course.group.empty": "这个辅导区还没有课程",
    "course.empty.lessons.title": "还没有课程",
    "course.empty.lessons.desc": "选择一个辅导区，用教材章节或主题一键生成可讲授的课件。",
    "course.empty.noWs.desc": "按科目或学习目标创建工作学习区，整理教材，开始准备第一堂课。",
  },
  en: {
    ...CLASSROOM_LIST_STRINGS.en,
    "course.eyebrow": "YOUR LESSON STUDIO",
    "course.art.title": "A little clarity.\nA lot of discovery.",
    "course.newSpace": "New workspace",
    "course.stat.spaces": "Workspaces",
    "course.stat.lessons": "Lessons",
    "course.stat.progress": "Preparing",
    "course.workflow": "Prepare · Refine · Teach",
    "course.library": "My lessons",
    "course.library.hint": "Organized by workspace, ready for your next session.",
    "course.search": "Find a workspace",
    "course.search.empty": "No matching workspaces",
    "course.subtitle": "Lessons organized by tutoring space — prepare in one click, resume anytime",
    "course.resume.title": "Continue learning",
    "course.group.lessons": "%n lessons",
    "course.group.viewAll": "View all",
    "course.group.empty": "No lessons in this space yet",
    "course.empty.lessons.title": "No lessons yet",
    "course.empty.lessons.desc": "Pick a tutoring space and turn textbook chapters or a topic into a teachable lesson in one click.",
    "course.empty.noWs.desc": "Create a workspace for a subject or learning goal, add your materials, and prepare your first lesson.",
  },
};
