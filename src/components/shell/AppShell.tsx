import { Sidebar } from "./Sidebar";
import { MainWorkspace } from "./MainWorkspace";

export function AppShell() {
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg">
      <Sidebar />
      <MainWorkspace />
    </div>
  );
}
