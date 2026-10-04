import { resolve } from "node:path";

export function backendEnv(env) {
  const data = resolve(env.E2E_RUN_ROOT, "data");
  const front = `http://127.0.0.1:${env.E2E_FRONTEND_PORT}`;
  return { ...env, EDU_TEST_KEYLESS: "1", AUTH_MODE: "1",
    NEXT_TUTOR_DATA_DIR: data, TRACE_DIR: resolve(data, "traces"),
    CHROMA_DIR: resolve(data, "knowledge/vector_db"),
    EMBEDDING_PROVIDER: "off", EMBEDDING_API_KEY: "", COMPAT_API_KEY: "",
    ADMIN_EMAIL: "material-admin@e2e.example.com", ADMIN_PASSWORD: "e2e-pass-123",
    OPENAI_API_KEY: "", AZURE_SPEECH_KEY: "", AZURE_SPEECH_REGION: "",
    TAVILY_API_KEY: "", PEXELS_API_KEY: "", PIXABAY_API_KEY: "",
    LLM_BASE_URL: `http://127.0.0.1:${env.E2E_LLM_PORT}/v1`,
    LLM_API_KEY: "fake-e2e-key", LLM_MODEL: "fake-llm", LLM_PROVIDER: "openai_compatible",
    LLM_SUPPORTS_IMAGES: "1",
    SITE_ASSISTANT_ENABLED: "1", QUIZ_SVG_ENABLED: "1",
    QUIZ_ILLUSTRATION_PIPELINE: "shadow", QUIZ_ILLUSTRATION_VISUAL_REVIEW: "active",
    SUPERVISOR_LLM_PLAN: "0", TEXTBOOK_GRAPH_ENABLED: "0",
    AUTH_JWT_SECRET: "e2e-test-secret-e2e-test-secret-e2e", RATE_LIMIT_DISABLE: "1",
    VOICE_TTS_PROVIDER: "stub", CORS_ORIGINS: `${front},http://localhost:${env.E2E_FRONTEND_PORT}`,
  };
}
