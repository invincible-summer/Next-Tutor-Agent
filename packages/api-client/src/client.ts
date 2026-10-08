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
import { createGuestClient, type GuestClient } from "./guest.ts";
import { createAssessmentClient, type AssessmentClient } from "./assessment.ts";
import { createCapabilitiesClient, type CapabilitiesClient } from "./capabilities.ts";
import { createClassroomClient, type ClassroomClient } from "./classroom.ts";
import { createDiagramsClient, type DiagramsClient } from "./diagrams.ts";
import { createKnowledgeClient, type KnowledgeClient } from "./knowledge.ts";
import { createLearningClient, type LearningClient } from "./learning.ts";
import { createLibraryClient, type LibraryClient } from "./library.ts";
import { createNotesClient, type NotesClient } from "./notes.ts";
import { createVoiceClient, type VoiceClient } from "./voice.ts";
import { createProfileClient, type ProfileClient } from "./profile.ts";
import { createUxClient, type UxClient } from "./ux.ts";
import { createEvaluationClient, type EvaluationClient } from "./evaluation.ts";
import { createMemoryClient, type MemoryClient } from "./memory.ts";
import { createArchiveClient, type ArchiveClient } from "./archive.ts";
import { createAssistantClient, type AssistantClient } from "./assistant.ts";
import {
  createToolIllustrationClient,
  type ToolIllustrationClient,
} from "./tools/illustration.ts";
import {
  createToolChemLabClient,
  type ToolChemLabClient,
} from "./tools/chem-lab.ts";
import { createImageClient, type ImageClient } from "./tools/image.ts";
import { createWorksheetClient, type WorksheetClient } from "./tools/worksheet.ts";

export interface ApiClient {
  readonly transport: Transport;
  readonly baseUrl: string;
  readonly auth: AuthClient;
  readonly chat: ChatClient;
  readonly workspace: WorkspaceClient;
  readonly illustration: IllustrationClient;
  readonly guest: GuestClient;
  readonly assessment: AssessmentClient;
  readonly capabilities: CapabilitiesClient;
  readonly classroom: ClassroomClient;
  readonly diagrams: DiagramsClient;
  readonly knowledge: KnowledgeClient;
  readonly learning: LearningClient;
  readonly library: LibraryClient;
  readonly notes: NotesClient;
  readonly voice: VoiceClient;
  readonly profile: ProfileClient;
  readonly ux: UxClient;
  readonly evaluation: EvaluationClient;
  readonly memory: MemoryClient;
  readonly archive: ArchiveClient;
  readonly assistant: AssistantClient;
  readonly tools: { illustration: ToolIllustrationClient; image: ImageClient; worksheet: WorksheetClient; chemLab: ToolChemLabClient };
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
    guest: createGuestClient(transport),
    assessment: createAssessmentClient(transport),
    capabilities: createCapabilitiesClient(transport),
    classroom: createClassroomClient(transport),
    diagrams: createDiagramsClient(transport),
    knowledge: createKnowledgeClient(transport),
    learning: createLearningClient(transport),
    library: createLibraryClient(transport),
    notes: createNotesClient(transport),
    voice: createVoiceClient(transport),
    profile: createProfileClient(transport),
    ux: createUxClient(transport),
    evaluation: createEvaluationClient(transport),
    memory: createMemoryClient(transport),
    archive: createArchiveClient(transport),
    assistant: createAssistantClient(transport),
    tools: {
      illustration: createToolIllustrationClient(transport),
      image: createImageClient(transport),
      worksheet: createWorksheetClient(transport),
      chemLab: createToolChemLabClient(transport),
    },
  };
}
