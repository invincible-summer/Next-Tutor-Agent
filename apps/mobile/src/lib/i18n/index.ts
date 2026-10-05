import {
  createTranslator,
  type Lang,
  type MessageDict,
  type Translator,
} from "@next-tutor/i18n";

import { en as baseEn } from "./dicts/base.en";
import { zh as baseZh } from "./dicts/base.zh";
import { enDicts, registerDicts, zhDicts } from "./registry";

// base 域最先注册；feature 域字典（features/<domain>/strings.ts 或
// lib/i18n/dicts/<domain>.*.ts）随模块加载陆续 push，后注册的同名片段覆盖先注册的。
registerDicts(baseZh, baseEn);

function merge(dicts: MessageDict[]): MessageDict {
  return Object.assign({}, ...dicts);
}

export function makeTranslator(lang: Lang): Translator {
  return createTranslator({ zh: merge(zhDicts), en: merge(enDicts) }, lang);
}

export { registerDicts };
export type { Lang, MessageDict, Translator };
