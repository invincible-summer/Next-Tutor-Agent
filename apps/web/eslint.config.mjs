import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    ".next-demo/**",
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // 本地跑 Playwright 生成的报告/产物（.gitignore 同款）：其中的打包
    // JS 不是源码，无参数全仓库 lint 时必须跳过。
    "playwright-report/**",
    "test-results/**",
    ".playwright/**",
  ]),
  {
    // Playwright E2E：动态 JSON 响应用 any 是刻意选择（断言层关心字段
    // 存在性而非静态形状），与产品代码的类型纪律分开。
    files: ["tests/e2e/**/*.ts", "tests/e2e/**/*.mjs"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": "off",
    },
  },
]);

export default eslintConfig;
