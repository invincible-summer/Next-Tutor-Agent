export const worksheetText = {
  zh: {
    back: "返回工具助手", title: "组卷出题", intro: "先明确试卷边界，再逐题生成、对话修改、预览和打印。", new: "新建试卷", resume: "继续编辑", titleField: "试卷标题", subject: "学科", grade: "年级", unit: "单元 / 知识范围", duration: "考试时长（分钟）", learningArea: "学习区", learningAreaRequired: "请选择真实学习区", learningAreaEmpty: "还没有可用学习区，请先创建一个学习区。", instructions: "考试说明", guidance: "补充命题提示", goal: "出卷目标与要求", goalPlaceholder: "例如：检测一次函数图像与解析式的综合运用，题目从基础到迁移逐步加深。", knowledgeGraph: "知识图谱点（可选）", knowledgeSearch: "搜索知识点", knowledgePlaceholder: "选择下方知识点，或输入多个知识点并用逗号分隔。", textbook: "参考教材进行知识检索", textbookHint: "只有选了知识点才会检索；不选知识点时默认关闭教材检索。", create: "创建并进入工作台", saved: "服务端草稿", empty: "还没有服务端草稿。", questions: "题目", count: "生成数量", type: "题型", difficulty: "难度", score: "每题分值", points: "知识点", generate: "批量生成", singleGenerate: "单题出题", batchGenerate: "批量生成", generating: "生成中…", add: "增加一题", save: "保存题目", savedToast: "已保存", preview: "单题预览", wholePreview: "整卷预览", editor: "编辑当前题", conversation: "出题对话", conversationHint: "告诉助手如何调整当前题，修改会保留在这道题上。", assistant: "助手", you: "你", answer: "答案与解析", hideAnswer: "隐藏答案", showAnswer: "显示答案", refine: "让助手修改", instruction: "修改要求", refinePlaceholder: "例如：把情境换成校园实验，保留公式与难度。", apply: "发送修改", exportStudent: "导出学生版", exportTeacher: "导出教师版", exportHtml: "导出 HTML", print: "打印 / PDF", noQuestions: "先点击“单题出题”开始添加题目。", select: "选择一道题", stem: "题干", options: "选项（每行一个，格式 A: 内容）", answerField: "答案", explanation: "解析", knowledge: "题目知识点", image: "题目配图", generateImage: "生成配图", imageUnavailable: "当前生图结果无法保存为试卷素材，请重试。", delete: "删除题目", deleteSheet: "删除试卷", conflict: "草稿版本已变化，请重新载入。", configured: "已配置", status: "已保存到服务端", multiple_choice: "选择题", fill_blank: "填空题", short_answer: "简答题", draft: "草稿", ready: "已生成", setup: "试卷设置", close: "关闭", teacherOnly: "教师版包含答案和解析。", stepScope: "范围与学习区", stepGuidance: "目标与命题依据", continue: "继续设置", previous: "返回上一步", setupHint: "学习区是必选范围；知识点、教材检索和出卷目标会一起约束后续生成。", paperStyle: "统一专业版式", paperStyleDesc: "A4 纸张比例、清晰题号和留白，适合打印与导出。", batchHint: "批量只在你主动打开后执行；日常编辑按一题一题推进。", previewHint: "整卷预览单独打开，当前区域只聚焦这一题。", noConcepts: "当前学习区还没有可用的图谱知识点，可手动输入。", reviseDone: "已根据你的要求更新当前题目。", reviseStart: "我会围绕当前题目处理你的修改要求。", areaSummary: "学习区", pointsSummary: "知识点", textbookOn: "教材检索已开启", textbookOff: "教材检索未开启",
  },
  en: {
    back: "Back to tools", title: "Worksheet builder", intro: "Set the scope first, then add questions one by one, revise by chat, preview and print.", new: "New paper", resume: "Continue editing", titleField: "Paper title", subject: "Subject", grade: "Grade", unit: "Unit / scope", duration: "Duration (minutes)", learningArea: "Learning area", learningAreaRequired: "Choose a real learning area", learningAreaEmpty: "No learning areas are available yet. Create one before authoring.", instructions: "Instructions", guidance: "Additional authoring hint", goal: "Worksheet goal & requirements", goalPlaceholder: "For example: check linear-function graphs and equations, moving from core skills to transfer.", knowledgeGraph: "Knowledge graph points (optional)", knowledgeSearch: "Search knowledge points", knowledgePlaceholder: "Choose graph points below, or type several separated by commas.", textbook: "Use textbook retrieval", textbookHint: "Retrieval runs only when knowledge points are selected; with none selected it stays off.", create: "Create and open builder", saved: "Server drafts", empty: "No server drafts yet.", questions: "questions", count: "Question count", type: "Question type", difficulty: "Difficulty", score: "Points each", points: "Knowledge points", generate: "Batch generate", singleGenerate: "Add one question", batchGenerate: "Batch generate", generating: "Generating…", add: "Add a question", save: "Save question", savedToast: "Saved", preview: "Single-question preview", wholePreview: "Full paper preview", editor: "Edit current question", conversation: "Authoring chat", conversationHint: "Tell the assistant how to adjust this question; the change stays with this question.", assistant: "Assistant", you: "You", answer: "Answer & explanation", hideAnswer: "Hide answers", showAnswer: "Show answers", refine: "Ask assistant to revise", instruction: "Revision request", refinePlaceholder: "For example: move this into a school lab context while keeping the formula and difficulty.", apply: "Send revision", exportStudent: "Export student version", exportTeacher: "Export teacher version", exportHtml: "Export HTML", print: "Print / PDF", noQuestions: "Click “Add one question” to begin.", select: "Select a question", stem: "Stem", options: "Options (one per line, format A: text)", answerField: "Answer", explanation: "Explanation", knowledge: "Question knowledge points", image: "Question image", generateImage: "Generate image", imageUnavailable: "This image result cannot be saved as a worksheet asset. Please try again.", delete: "Delete question", deleteSheet: "Delete paper", conflict: "The draft changed on another device. Reload it before saving.", configured: "Configured", status: "Saved on the server", multiple_choice: "Multiple choice", fill_blank: "Fill in the blank", short_answer: "Short answer", draft: "Draft", ready: "Ready", setup: "Paper settings", close: "Close", teacherOnly: "Teacher version includes answers and explanations.", stepScope: "Scope & learning area", stepGuidance: "Goal & grounding", continue: "Continue", previous: "Back", setupHint: "A learning area is required; graph points, textbook retrieval and the worksheet goal guide later generation.", paperStyle: "Unified professional layout", paperStyleDesc: "A4 proportions, clear numbering and generous whitespace for print and export.", batchHint: "Batch generation runs only when you open it; everyday editing stays one question at a time.", previewHint: "Full-paper preview opens separately; this panel stays focused on the active question.", noConcepts: "No graph points are available for this area yet; you can type them manually.", reviseDone: "The current question was updated from your request.", reviseStart: "I’ll apply your revision to the current question.", areaSummary: "Area", pointsSummary: "Points", textbookOn: "Textbook retrieval on", textbookOff: "Textbook retrieval off",
  },
} as const;


export const worksheetExtra = {
  zh: {
    loading: "正在载入试卷…", loadFailed: "暂时无法载入，请重试。", retry: "重新载入",
    createArea: "创建学习区", manageArea: "管理学习区教材", areaMissing: "请在试卷设置中补齐有效学习区后继续出题。",
    list: "我的试卷", saveSettings: "保存设置", cancel: "取消", deleteWarning: "删除后无法恢复，请确认已导出需要保留的内容。",
    searchDraft: "搜索试卷标题", selectedCount: "已选知识点", noMatches: "没有匹配的知识点，试试其他关键词。",
    conceptsLoading: "正在载入图谱知识点…", conceptsFailed: "图谱暂时无法载入，可重试或手动填写知识点。",
    clearPoints: "清空选择", pointLimit: "最多选择 12 个知识点", noTextbooks: "当前学习区未选择教材。请先管理学习区教材，再开启检索。",
    mathHint: "公式支持 $…$、$$…$$、\\(…\\) 和 \\[…\\]；预览与打印会渲染。",
    conversationEmpty: "先描述这一题的要求，或点击“单题出题”。我会按试卷目标和已选范围逐题生成。",
    newPlaceholder: "描述下一题的考查目标、题型或情境…", newMode: "出下一题", reviseMode: "修改本题",
    sendNew: "生成这一题", chatLocal: "本次编辑对话", quickContext: "换一个生活化情境，保留考查目标。", quickSteps: "完善解题步骤，并检查公式和答案。", quickChallenge: "增加一点难度，保留知识点和题型。",
    requestFailed: "操作未完成，请稍后重试。", scopeEmpty: "当前学习区没有可检索教材，请在学习区中选择教材。",
    retrievalFailed: "教材检索暂不可用，请重试。", noEvidence: "选定教材没有匹配证据，请调整知识点或关闭教材检索。",
    generationFailed: "题目生成失败，没有追加题目。请调整要求后重试。", refineFailed: "本题修改未完成，原题已保留，请重试。",
    fallback: "生成服务暂不可用，已添加可手工编辑的草稿。", mathInvalid: "存在无法解析的公式，请在题目编辑器中修正后打印。",
    printFailed: "打印页面未能准备完成，请重试。", preparingPrint: "准备打印…",
    studentVersion: "学生版", teacherVersion: "教师版", exportMarkdown: "导出 Markdown", exportHtml: "导出 HTML",
    paperDuration: "考试时间（分钟）：", paperTotal: "满分：", studentName: "姓名：", studentClass: "班级：", pointsUnit: "分",
    defaultInstructions: "请认真审题，按题目要求作答。", previewNoQuestions: "添加题目后可以预览和打印整卷。",
    scoreTotal: "总分", generationHint: "本次出题要求（可选）", generated: "已追加题目", partialGenerated: "部分题目未生成，可以调整要求后补充。",
    saveHint: "手动修改在点击“保存题目”后写入服务端。", editHint: "调整题干、选项、答案与解析。支持 Markdown 和 LaTeX。",
    selectedQuestion: "当前题", deleteQuestionWarning: "删除当前题后，其余题号和总分会重新计算。",
    paperPreviewHint: "学生版隐藏答案与解析，教师版展示完整解答。打印采用独立 A4 页面。",
  },
  en: {
    loading: "Loading paper…", loadFailed: "Could not load the paper. Please retry.", retry: "Reload",
    createArea: "Create learning area", manageArea: "Manage area textbooks", areaMissing: "Choose a valid learning area in paper settings before generating questions.",
    list: "My papers", saveSettings: "Save settings", cancel: "Cancel", deleteWarning: "Deletion cannot be undone. Export anything you want to keep first.",
    searchDraft: "Search paper titles", selectedCount: "Selected points", noMatches: "No matching points. Try another search.",
    conceptsLoading: "Loading graph points…", conceptsFailed: "The graph is unavailable. Retry or type knowledge points.",
    clearPoints: "Clear selection", pointLimit: "Choose up to 12 points", noTextbooks: "No textbooks are selected in this area. Add them before enabling retrieval.",
    mathHint: "Formulas support $…$, $$…$$, \\(…\\) and \\[…\\]. They render in preview and print.",
    conversationEmpty: "Describe the first question, or click “Add one question”. I’ll use the paper goal and selected scope.",
    newPlaceholder: "Describe the next question’s goal, type or context…", newMode: "Next question", reviseMode: "Revise this question",
    sendNew: "Generate this question", chatLocal: "This editing session", quickContext: "Use an everyday context while keeping the goal.", quickSteps: "Expand the solution steps and check the formulas and answer.", quickChallenge: "Make it slightly harder while keeping the topic and type.",
    requestFailed: "Could not complete this action. Please retry.", scopeEmpty: "No searchable textbooks are selected in this area. Add them in area settings.",
    retrievalFailed: "Textbook retrieval is unavailable. Please retry.", noEvidence: "No matching textbook evidence. Adjust the points or turn retrieval off.",
    generationFailed: "Generation failed; no question was added. Adjust the request and retry.", refineFailed: "Revision failed. The original question was kept; please retry.",
    fallback: "Generation is unavailable. An editable draft was added.", mathInvalid: "A formula could not be parsed. Correct it in the editor before printing.",
    printFailed: "The print page could not be prepared. Please retry.", preparingPrint: "Preparing print…",
    studentVersion: "Student version", teacherVersion: "Teacher version", exportMarkdown: "Export Markdown", exportHtml: "Export HTML",
    paperDuration: "Duration (minutes):", paperTotal: "Total:", studentName: "Name:", studentClass: "Class:", pointsUnit: "pts",
    defaultInstructions: "Read each question carefully and follow the instructions.", previewNoQuestions: "Add questions to preview and print the paper.",
    scoreTotal: "Total", generationHint: "This generation request (optional)", generated: "Questions added", partialGenerated: "Some questions could not be generated. Adjust the request and add them again.",
    saveHint: "Manual changes are saved to the server when you click “Save question”.", editHint: "Edit the stem, options, answer and explanation. Markdown and LaTeX are supported.",
    selectedQuestion: "Current question", deleteQuestionWarning: "Deleting this question recalculates the remaining question numbers and total.",
    paperPreviewHint: "The student version hides answers; the teacher version includes solutions. Printing uses a standalone A4 page.",
  },
} as const;

export type WorksheetCopy = (typeof worksheetText.zh & typeof worksheetExtra.zh)
  | (typeof worksheetText.en & typeof worksheetExtra.en);
