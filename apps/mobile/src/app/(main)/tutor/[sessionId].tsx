import { useLocalSearchParams } from "expo-router";

import { ChatScreen } from "@/features/chat";

export default function TutorSessionRoute() {
  const params = useLocalSearchParams<{ sessionId?: string }>();
  const raw = params.sessionId ?? null;
  let sessionId = raw;
  if (raw) {
    try {
      sessionId = decodeURIComponent(raw);
    } catch {
      sessionId = raw;
    }
  }
  return <ChatScreen sessionId={sessionId} />;
}
