import ClientPage from "./client";
import { demoRoutes } from "@/lib/demo-routes";

export function generateStaticParams() {
  return [{ sessionId: [] as string[] }, ...demoRoutes().sessions.map((id) => ({ sessionId: [id] }))];
}

export default function Page() {
  return <ClientPage />;
}
