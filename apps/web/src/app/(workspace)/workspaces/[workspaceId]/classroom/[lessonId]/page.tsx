import ClientPage from "./client";
import { demoRoutes } from "@/lib/demo-routes";
import { DemoLessonViewer } from "@/components/classroom/DemoLessonViewer";

export function generateStaticParams() {
  return demoRoutes().lessons;
}

export default function Page() {
  return process.env.NEXT_PUBLIC_DEMO_MODE === "1" ? <DemoLessonViewer /> : <ClientPage />;
}
