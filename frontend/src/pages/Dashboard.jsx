import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RTooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from "recharts";
import { Users, Briefcase, FolderKanban, ListChecks, PlayCircle, FileSpreadsheet } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import PageHeader from "@/components/PageHeader";
import StatCard from "@/components/StatCard";
import AsyncState from "@/components/AsyncState";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { priorityVariant, scheduleVariant } from "@/lib/format";

const COLORS = ["#0f172a", "#475569", "#94a3b8", "#cbd5e1"];

export default function Dashboard() {
  const navigate = useNavigate();
  const overview = useAsync(api.overview, []);
  const pipeline = useAsync(api.pipelineProjects, []);
  const projects = useAsync(api.projects, []);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const priorityChart = useMemo(() => {
    if (!pipeline.data) return [];
    const counts = {};
    pipeline.data.forEach((p) => {
      const key = p.client_priority || "Other";
      counts[key] = (counts[key] || 0) + 1;
    });
    return Object.entries(counts).map(([name, value]) => ({ name, value }));
  }, [pipeline.data]);

  const healthPie = useMemo(() => {
    if (!projects.data) return [];
    const counts = { GREEN: 0, AMBER: 0, RED: 0, NO_COLOR: 0 };
    projects.data.forEach((p) => {
      const s = (p.latest_schedule || "NO_COLOR").toUpperCase();
      counts[s] = (counts[s] || 0) + 1;
    });
    return Object.entries(counts)
      .filter(([, v]) => v > 0)
      .map(([name, value]) => ({ name, value }));
  }, [projects.data]);

  const topPipeline = useMemo(() => {
    if (!pipeline.data) return [];
    return [...pipeline.data]
      .sort((a, b) => (b.composite_priority || 0) - (a.composite_priority || 0))
      .slice(0, 8);
  }, [pipeline.data]);

  async function runAll() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.runAll();
      setMsg(r.message || "Run complete.");
      pipeline.reload();
    } catch (e) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function genExcel() {
    setBusy(true);
    setMsg(null);
    try {
      await api.generateExcel();
      window.location.href = api.downloadExcelUrl();
    } catch (e) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Resourcing Overview"
        description="Live snapshot of demand, people, and project health across CoLab."
      >
        <Button variant="outline" size="sm" onClick={genExcel} disabled={busy}>
          <FileSpreadsheet className="mr-1.5 h-4 w-4" />
          Export Excel
        </Button>
        <Button size="sm" onClick={runAll} disabled={busy}>
          <PlayCircle className="mr-1.5 h-4 w-4" />
          Run All Recommendations
        </Button>
      </PageHeader>

      <div className="space-y-6 p-6">
        {msg && (
          <div className="rounded-md border bg-muted/40 px-3 py-2 text-sm">{msg}</div>
        )}

        <AsyncState loading={overview.loading} error={overview.error}>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard icon={Users} label="People in Pool" value={overview.data?.people_count ?? "—"} />
            <StatCard icon={FolderKanban} label="Active Projects" value={overview.data?.projects_count ?? "—"} />
            <StatCard icon={Briefcase} label="Pipeline Requests" value={overview.data?.pipeline_count ?? "—"} />
            <StatCard icon={ListChecks} label="Open Role Slots" value={overview.data?.roles_count ?? "—"} />
          </div>
        </AsyncState>

        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Pipeline demand by client priority</CardTitle>
              <CardDescription>Count of pipeline projects per priority tier</CardDescription>
            </CardHeader>
            <CardContent>
              <AsyncState loading={pipeline.loading} error={pipeline.error}>
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={priorityChart}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="name" fontSize={12} />
                    <YAxis fontSize={12} allowDecimals={false} />
                    <RTooltip />
                    <Bar dataKey="value" fill="#0f172a" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </AsyncState>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Project health (schedule)</CardTitle>
              <CardDescription>Latest weekly status across active projects</CardDescription>
            </CardHeader>
            <CardContent>
              <AsyncState loading={projects.loading} error={projects.error}>
                <ResponsiveContainer width="100%" height={260}>
                  <PieChart>
                    <Pie data={healthPie} dataKey="value" nameKey="name" innerRadius={50} outerRadius={85}>
                      {healthPie.map((entry, i) => (
                        <Cell
                          key={entry.name}
                          fill={
                            entry.name === "GREEN"
                              ? "#10b981"
                              : entry.name === "AMBER"
                              ? "#f59e0b"
                              : entry.name === "RED"
                              ? "#ef4444"
                              : "#94a3b8"
                          }
                        />
                      ))}
                    </Pie>
                    <Legend />
                    <RTooltip />
                  </PieChart>
                </ResponsiveContainer>
              </AsyncState>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Top priority pipeline requests</CardTitle>
            <CardDescription>Highest composite priority — click a row to view recommendations</CardDescription>
          </CardHeader>
          <CardContent>
            <AsyncState loading={pipeline.loading} error={pipeline.error}>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Client</TableHead>
                    <TableHead>Priority</TableHead>
                    <TableHead>Request</TableHead>
                    <TableHead>Solution</TableHead>
                    <TableHead>Start</TableHead>
                    <TableHead>SOW</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {topPipeline.map((p) => (
                    <TableRow
                      key={p.pipeline_id}
                      className="cursor-pointer"
                      onClick={() => navigate(`/pipeline/${encodeURIComponent(p.pipeline_id)}`)}
                    >
                      <TableCell className="font-medium">{p.client}</TableCell>
                      <TableCell>
                        <Badge variant={priorityVariant(p.client_priority)}>{p.client_priority || "—"}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={priorityVariant(p.priority)}>{p.priority || "—"}</Badge>
                      </TableCell>
                      <TableCell className="max-w-[160px] truncate">{p.solution || "—"}</TableCell>
                      <TableCell>{p.likely_start_str || "TBD"}</TableCell>
                      <TableCell>{p.sow_signed ? "Yes" : "No"}</TableCell>
                      <TableCell>
                        {p.allocated ? (
                          <Badge variant="success">Allocated</Badge>
                        ) : (
                          <Badge variant="secondary">Pending</Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </AsyncState>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
