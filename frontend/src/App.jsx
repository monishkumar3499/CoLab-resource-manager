import React from "react";
import { Routes, Route, NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Users,
  Briefcase,
  HeartPulse,
  BarChart3,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import Dashboard from "@/pages/Dashboard";
import PipelineProjects from "@/pages/PipelineProjects";
import RecommendationDetail from "@/pages/RecommendationDetail";
import ResourcePool from "@/pages/ResourcePool";
import ProjectHealth from "@/pages/ProjectHealth";
import Analytics from "@/pages/Analytics";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/pipeline", label: "Pipeline Demand", icon: Briefcase },
  { to: "/resources", label: "Resource Pool", icon: Users },
  { to: "/health", label: "Project Health", icon: HeartPulse },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
];

export default function App() {
  return (
    <div className="flex min-h-screen w-full">
      <aside className="hidden w-60 shrink-0 border-r bg-muted/30 md:flex md:flex-col">
        <div className="flex items-center gap-2 border-b px-4 py-4">
          <Sparkles className="h-5 w-5" />
          <div>
            <p className="text-sm font-semibold leading-none">CoLab RMG</p>
            <p className="text-xs text-muted-foreground">Resourcing Copilot</p>
          </div>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                  isActive && "bg-accent text-accent-foreground"
                )
              }
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t p-3 text-xs text-muted-foreground">
          Stateful allocation engine · v1.0
        </div>
      </aside>

      <main className="flex-1 overflow-x-hidden">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/pipeline" element={<PipelineProjects />} />
          <Route path="/pipeline/:pipelineId" element={<RecommendationDetail />} />
          <Route path="/resources" element={<ResourcePool />} />
          <Route path="/health" element={<ProjectHealth />} />
          <Route path="/analytics" element={<Analytics />} />
        </Routes>
      </main>
    </div>
  );
}
