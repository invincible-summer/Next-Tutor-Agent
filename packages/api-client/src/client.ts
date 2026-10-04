/**
 * Client assembly (docs/architecture/client-platform.md): one injector constructor wires the transport
 * pipeline to the domain clients. Platforms call `createApiClient` with their
 * own adapters (browser fetch / expo fetch, token storage, metadata) — this
 * module must never read env vars, `window` or SecureStore.
 */
import type { Transport } from "./types.ts";
import { createTransport, type ApiClientConfig } from "./transport.ts";
import { createAuthClient, type AuthClient } from "./auth.ts";
import { createChatClient, type ChatClient } from "./chat.ts";
import { createWorkspaceClient, type WorkspaceClient } from "./workspace.ts";
import { createIllustrationClient, type IllustrationClient } from "./illustration.ts";
import {
  createToolIllustrationClient,
  type ToolIllustrationClient,
} from "./tools/illustration.ts";

export interface ApiClient {
  readonly transport: Transport;
  readonly baseUrl: string;
  readonly auth: AuthClient;
  readonly chat: ChatClient;
  readonly workspace: WorkspaceClient;
  readonly illustration: IllustrationClient;
  readonly tools: { illustration: ToolIllustrationClient };
}

export function createApiClient(config: ApiClientConfig): ApiClient {
  const transport = createTransport(config);
  return {
    transport,
    baseUrl: transport.baseUrl,
    auth: createAuthClient(transport),
    chat: createChatClient(transport),
    workspace: createWorkspaceClient(transport),
    illustration: createIllustrationClient(transport),
    tools: { illustration: createToolIllustrationClient(transport) },
  };
}
