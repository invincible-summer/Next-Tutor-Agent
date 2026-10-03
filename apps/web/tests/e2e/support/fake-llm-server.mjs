/**
 * Fake OpenAI-compatible LLM server for E2E.
 *
 * E2E 测产品编排与数据流，不测供应商网络：所有 LLM 调用指向这里。
 * 协议：POST /chat/completions（stream 与非 stream 都支持）。
 * 路由策略按 prompt 特征返回固定内容：
 *   - 蓝图（命题设计专家） / 出题（出题专家/命题专家） / 审题（审题员）
 *   - executor 工具循环：携带 tools 且学生消息含出题意图 -> tool_calls
 *   - 其余（讲解合成/理解/规划等）-> 稳定中文回答
 */
import http from "node:http";

const PORT = Number(process.env.FAKE_LLM_PORT || 8199);

const BLUEPRINT = JSON.stringify({
  items: [
    { target_claims: ["识别 ZX-17 定理的核心常数"],
      intended_processes: ["understand"], q_type: "multiple_choice",
      construction_brief: "用教材原文中的核心常数设置识别题。" },
    { target_claims: ["在题干情境中正确使用 ZX-17 定理"],
      intended_processes: ["apply"], q_type: "fill_blank",
      construction_brief: "用新的题干表述检查定理常数的迁移应用。" },
  ],
  requirements: [
    { question_slot: "q1", illustration_needed: true, scene_brief: "水平面滑块",
      needs: [{ key: "scene", name: "水平面滑块", quantity: 1, features: [], category: "physics" }],
      visible_labels: [], layout_intent: "主体居中，四周留白" },
    { question_slot: "q2", illustration_needed: true, scene_brief: "水平面滑块",
      needs: [{ key: "scene", name: "水平面滑块", quantity: 1, features: [], category: "physics" }],
      visible_labels: [], layout_intent: "主体居中，四周留白" },
  ],
});

const QUESTIONS = JSON.stringify({
  questions: [
    { id: 1, type: "multiple_choice",
      stem: "根据教材，ZX-17 定理的右端常数是多少？",
      options: { A: "314159", B: "271828", C: "141421", D: "161803" },
      answer: "A",
      explanation: "教材原文写明 ZX-17 定理的右端常数为 314159；其余选项是其它数学常数的近似值，属于干扰项。",
      knowledge_point: "ZX-17 定理", difficulty: "easy", bloom_level: "remember",
      source_ref_ids: ["src_1"] },
    { id: 2, type: "fill_blank",
      stem: "在 ZX-17 定理的表述中，右端常数等于______。",
      answer: "314159",
      explanation: "教材中 ZX-17 定理的右端常数唯一确定，为 314159；填其它数值均不符合定理原文。",
      knowledge_point: "ZX-17 定理", difficulty: "easy", bloom_level: "remember",
      source_ref_ids: ["src_1", "src_2"] },
  ],
});

const CRITIC_OK = JSON.stringify({
  verdicts: [
    { id: 1, verdict: "correct", reason: "与拟定答案一致" },
    { id: 2, verdict: "correct", reason: "与拟定答案一致" },
  ],
});
const CRITIC_WITH_ILLUSTRATION = JSON.stringify({
  items: [
    { question_ref: "1", proposed_status: "passed", answer_check: "与拟定答案一致", alignment: "aligned", brief_basis: "题干、答案与图示一致", illustration_check: "passed", illustration_issues: [] },
    { question_ref: "2", proposed_status: "passed", answer_check: "与拟定答案一致", alignment: "aligned", brief_basis: "题干、答案与图示一致", illustration_check: "passed", illustration_issues: [] },
  ],
});

function pickCompletion(body) {
  const text = (body.messages || []).map((m) => m.content || "").join("\n");
  const userMsgs = (body.messages || []).filter((m) => m.role === "user");
  const userText = userMsgs.map((m) => String(m.content)).join("\n");
  const requiresIllustration = text.includes("illustration_policy=required");
  const auditsIllustrations = text.includes("illustration_check");
  const scene = { schema_version: 1, width: 640, height: 400, profile: "textbook",
    nodes: [{ id: "scene", asset_id: "template.horizontal_block", version: 1, x: 16, y: 16, scale: .9, rotation: 0, params: {}, label: "" }],
    connections: [], labels: [], alt: "水平面上的物体示意图", caption: "示意图" };
  if (text.includes("教材题图构想师")) {
    return JSON.stringify({ requirements: Array.from({ length: text.includes("冻结文字题") ? 1 : 2 }, (_, i) => ({
      question_slot: `q${i+1}`, illustration_needed: requiresIllustration || !userText.includes('"illustration_policy": "auto"'),
      scene_brief: "水平面滑块", needs: [{ key: "scene", name: "水平面滑块", quantity: 1, features: [] }],
      visible_labels: [], layout_intent: "主体居中" })) });
  }
  if (text.includes("补充题图独立审图")) return JSON.stringify({ status: "passed", issues: [] });
  if (text.includes("精致教材组件构图合同")) {
    if (text.includes("冻结题只返回")) return JSON.stringify({ questions: [{ diagram_scene: scene }] });
    const result = JSON.parse(QUESTIONS);
    result.questions = result.questions.map(q => ({ ...q, illustration: null, diagram_scene: requiresIllustration ? scene : null }));
    return JSON.stringify(result);
  }
  // 出题三段（非 stream complete）
  if (text.includes("命题设计专家") || text.includes("本轮合并命题蓝图")) {
    const blueprint = JSON.parse(BLUEPRINT);
    if (!requiresIllustration) blueprint.requirements = blueprint.requirements.map(row => ({
      ...row, illustration_needed: false, needs: [], scene_brief: "", layout_intent: "",
    }));
    return JSON.stringify(blueprint);
  }
  if (text.includes("出题审核员") || text.includes("illustration_check")) {
    return auditsIllustrations ? CRITIC_WITH_ILLUSTRATION : CRITIC_OK;
  }
  if (text.includes("出题专家") || text.includes("你是命题专家")) {
    return QUESTIONS;
  }
  // executor 工具循环：学生要出题且模型可以调用工具 -> 调 generate_quiz。
  // "已出过题"只认 generate_quiz 的 tool 响应（preresearch 的检索结果
  // 也是 tool 消息，不能算）：成功（questions）/ strict 拒绝（未生成
  // 教材题）/ 重复调用拦截，三者都算"这轮出过题"，不再重试。
  // topic 按学生意图路由：ZX-999 走 strict NOT_FOUND 用例（教材里没有
  // 该知识点，不允许出伪教材题）。
  const quizAlready = (body.messages || []).some((m) =>
    m.role === "tool" &&
    /"questions"|未生成教材题|相同参数调用过/.test(String(m.content || "")));
  const wantsQuiz = /出题|练习|测一测|考我|出\s*\d*\s*道|道.*题/.test(userText);
  const hasQuizTool = (body.tools || []).some((t) =>
    (t.function?.name || "") === "generate_quiz");
  if (hasQuizTool && wantsQuiz && !quizAlready) {
    const topic = /ZX-999/.test(userText) ? "ZX-999 未定义概念" : "ZX-17 定理";
    return { __tool_calls: [{
      id: "call_quiz_1", type: "function",
      function: { name: "generate_quiz",
                   arguments: JSON.stringify(
                     { topic, grade: "本科", difficulty: "easy", count: 2 })},
    }] };
  }
  // 问答合成：preresearch 命中后的回答
  if (/ZX-17|教材|资料/.test(userText)) {
    return "根据刚上传教材的原文，ZX-17 定理的右端常数为 314159。";
  }
  return "好的，我们继续。这个知识点可以这样理解：先看定义，再看例子。";
}

function sseChunks(payload, finish = "stop") {
  // OpenAI streaming 兼容：一次 delta + finish + [DONE]
  const chunks = [];
  chunks.push({ id: "chatcmpl-fake", object: "chat.completion.chunk",
                choices: [{ index: 0, delta: payload, finish_reason: null }] });
  chunks.push({ id: "chatcmpl-fake", object: "chat.completion.chunk",
                choices: [{ index: 0, delta: {}, finish_reason: finish }] });
  return chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n";
}

function toolCallDeltas(calls) {
  // openai SDK 流式拼装要求每个 tool_call delta 带 index；一次性给全量
  // name+arguments（单 chunk 即完整调用）。
  return calls.map((c, i) => ({
    index: i, id: c.id, type: "function",
    function: { name: c.function.name, arguments: c.function.arguments },
  }));
}

const server = http.createServer((req, res) => {
  if (req.method !== "POST" || !req.url.includes("/chat/completions")) {
    res.writeHead(404).end();
    return;
  }
  let raw = "";
  req.on("data", (c) => (raw += c));
  req.on("end", () => {
    let body = {};
    try { body = JSON.parse(raw || "{}"); } catch { /* keep {} */ }
    const picked = pickCompletion(body);
    if (body.stream) {
      res.writeHead(200, { "Content-Type": "text/event-stream",
                           "Cache-Control": "no-cache" });
      if (picked && picked.__tool_calls) {
        // tool_calls 走标准增量格式（带 index）
        res.write(sseChunks({ role: "assistant",
                              tool_calls: toolCallDeltas(picked.__tool_calls) },
                            "tool_calls"));
      } else {
        res.write(sseChunks({ role: "assistant",
                              content: String(picked) }));
      }
      res.end();
      return;
    }
    const message = picked && picked.__tool_calls
      ? { role: "assistant", content: null, tool_calls: picked.__tool_calls }
      : { role: "assistant", content: String(picked) };
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({
      id: "chatcmpl-fake", object: "chat.completion",
      created: Date.now() / 1000 | 0, model: body.model || "fake-llm",
      choices: [{ index: 0, message, finish_reason:
                  picked && picked.__tool_calls ? "tool_calls" : "stop" }],
      usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 },
    }));
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[fake-llm] listening on 127.0.0.1:${PORT}`);
});
