import ClientPage from "./client";
import { demoRoutes } from "@/lib/demo-routes";

export function generateStaticParams() {
  return [{ noteId: [] as string[] }, ...demoRoutes().notes.map((id) => ({ noteId: [id] }))];
}

export default function Page() {
  return <ClientPage />;
}
