import type { MessageDict } from "@next-tutor/i18n";

/**
 * 移动端中文字典（base 域：common/nav/auth/home）。
 * feature 域字典在各 feature 目录下，经 registry.ts 注册合并。
 */
export const zh: MessageDict = {
  "common.ok": "确定",
  "common.cancel": "取消",
  "common.confirm": "确认",
  "common.save": "保存",
  "common.delete": "删除",
  "common.retry": "重试",
  "common.loading": "加载中…",
  "common.back": "返回",
  "common.done": "完成",
  "common.optional": "可选",
  "common.error.generic": "出了点问题，请稍后重试",

  "nav.home": "首页",
  "nav.tutor": "辅导",
  "nav.learn": "学习",
  "nav.library": "资料",
  "nav.me": "我的",

  "auth.signIn.title": "欢迎回来",
  "auth.signIn.subtitle": "继续你的学习旅程",
  "auth.register.title": "创建账号",
  "auth.register.subtitle": "开启个性化学习",
  "auth.email": "邮箱",
  "auth.password": "密码",
  "auth.password.hint": "至少 6 位",
  "auth.username": "昵称",
  "auth.name": "姓名",
  "auth.grade": "学段",
  "auth.signIn.action": "登录",
  "auth.register.action": "注册",
  "auth.signIn.toRegister": "还没有账号？注册",
  "auth.register.toSignIn": "已有账号？登录",
  "auth.guest": "先逛逛，不登录",
  "auth.guest.banner": "游客模式：学习记录不会保存",
  "auth.error.invalid_credentials": "邮箱或密码不正确",
  "auth.error.email_already_registered": "该邮箱已注册",
  "auth.grade.auto": "自动",
  "auth.grade.primary": "小学",
  "auth.grade.middle": "初中",
  "auth.grade.high": "高中",
  "auth.grade.undergraduate": "本科",

  "home.greeting.loading": "正在准备今日学习…",
  "home.continue": "继续学习",
  "home.quickActions": "快捷入口",
  "home.quick.chat": "问老师",
  "home.quick.assessment": "做测评",
  "home.quick.knowledge": "看图谱",
  "home.quick.tools": "情景配图",

  "empty.generic.title": "这里还是空的",
  "empty.generic.hint": "内容准备好后会出现在这里",
};
