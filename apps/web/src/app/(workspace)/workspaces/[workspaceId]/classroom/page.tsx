import ClientPage from "./client";
import { demoRoutes } from "@/lib/demo-routes";

export function generateStaticParams() {
  return demoRoutes().workspaces.map((id) => ({ workspaceId: id }));
}

export default function Page() {
  return <ClientPage />;
}
