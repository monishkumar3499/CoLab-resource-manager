import React, { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import PageHeader from "@/components/PageHeader";
import AsyncState from "@/components/AsyncState";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Input } from "@/components/ui/misc";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { healthVariant, scheduleVariant } from "@/lib/format";

export default function ProjectHealth() {
  const { data, loading, error } = useAsync(api.projects, []);
  const [search, setSearch] = useState("");
  const [scheduleFilter, setScheduleFilter] = useState("all");
  const [selected, setSelected] = useState(null);

  const filtered = useMemo(() => {
    if (!data) return [];
    return data
      .filter((p) => (search ? (p.project_id || "").toLowerCase().includes(search.toLowerCase()) || (p.CLIENT_ID || "").toLowerCase().includes(search.toLowerCase()) : true))
      .filter((p) => (scheduleFilter === "all" ? true : (p.latest_schedule || "NO_COLOR") === scheduleFilter))
      .sort((a, b) => (a.health_score ?? 1) - (b.health_score ?? 1));
  }, [data, search, scheduleFilter]);

  const counts = useMemo(() => {
    if (!data) return { RED: 0, AMBER: 0, GREEN: 0 };
    const c = { RED: 0, AMBER: 0, GREEN: 0 };
    data.forEach((p) => {
      const s = (p.latest_schedule || "").toUpperCase();
      if (c[s] !== undefined) c[s] += 1;
    });
    return c;
  }, [data]);

  return (
    <div>
      <PageHeader title="Project Health Monitoring" description="Health scores, extension risk, and weekly status across active engagements." />
      <div className="space-y-4 p-6">
        <div className="grid grid-cols-3 gap-3 md:max-w-lg">
          <Card>
            <CardContent className="p-3 text-center">
              <p className="text-xs text-muted-foreground">RED</p>
              <p className="text-xl font-semibold text-red-600">{counts.RED}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-3 text-center">
              <p className="text-xs text-muted-foreground">AMBER</p>
              <p className="text-xl font-semibold text-amber-600">{counts.AMBER}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-3 text-center">
              <p className="text-xs text-muted-foreground">GREEN</p>
              <p className="text-xl font-semibold text-emerald-600">{counts.GREEN}</p>
            </CardContent>
          </Card>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Input placeholder="Search project or client ID…" value={search} onChange={(e) => setSearch(e.target.value)} className="max-w-xs" />
          <Select value={scheduleFilter} onValueChange={setScheduleFilter}>
            <SelectTrigger className="w-44">
              <SelectValue placeholder="Schedule status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="GREEN">GREEN</SelectItem>
              <SelectItem value="AMBER">AMBER</SelectItem>
              <SelectItem value="RED">RED</SelectItem>
              <SelectItem value="NO_COLOR">No status</SelectItem>
            </SelectContent>
          </Select>
          <p className="ml-auto text-xs text-muted-foreground">{filtered.length} projects</p>
        </div>

        <Card>
          <CardContent className="p-0">
            <AsyncState loading={loading} error={error}>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Project</TableHead>
                    <TableHead>Client</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Health</TableHead>
                    <TableHead>Schedule</TableHead>
                    <TableHead>Ext. risk</TableHead>
                    <TableHead>Team</TableHead>
                    <TableHead>Billability</TableHead>
                    <TableHead>Days left</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filtered.map((p) => (
                    <TableRow key={p.project_id} className="cursor-pointer" onClick={() => setSelected(p)}>
                      <TableCell className="font-medium">{p.project_id}</TableCell>
                      <TableCell>{p.CLIENT_ID}</TableCell>
                      <TableCell className="max-w-[140px] truncate">{p.type_of_project}</TableCell>
                      <TableCell className="w-32">
                        <div className="flex items-center gap-2">
                          <Progress value={(p.health_score ?? 0) * 100} className="h-1.5 w-16" />
                          <Badge variant={healthVariant(p.health_score)}>{((p.health_score ?? 0) * 100).toFixed(0)}%</Badge>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={scheduleVariant(p.latest_schedule)}>{p.latest_schedule || "—"}</Badge>
                      </TableCell>
                      <TableCell>{((p.extension_risk ?? 0) * 100).toFixed(0)}%</TableCell>
                      <TableCell>{p.total_slots}</TableCell>
                      <TableCell>{(p.billability_rate ?? 0).toFixed(0)}%</TableCell>
                      <TableCell>{p.days_until_end ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </AsyncState>
          </CardContent>
        </Card>
      </div>

      <Dialog open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent>
          {selected && (
            <>
              <DialogHeader>
                <DialogTitle>{selected.project_id}</DialogTitle>
                <DialogDescription>
                  {selected.CLIENT_ID} · {selected.type_of_project} · {selected.tech_coe || "—"}
                </DialogDescription>
              </DialogHeader>

              <div className="grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">Health score</p>
                  <p className="text-lg font-semibold">{((selected.health_score ?? 0) * 100).toFixed(0)}%</p>
                </div>
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">Extension risk</p>
                  <p className="text-lg font-semibold">{((selected.extension_risk ?? 0) * 100).toFixed(0)}%</p>
                </div>
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">Ramp-down signal</p>
                  <p className="text-lg font-semibold">{((selected.ramp_down_signal ?? 0) * 100).toFixed(0)}%</p>
                </div>
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">Billability rate</p>
                  <p className="text-lg font-semibold">{(selected.billability_rate ?? 0).toFixed(0)}%</p>
                </div>
              </div>

              <div>
                <p className="mb-1.5 text-xs font-semibold uppercase text-muted-foreground">Latest weekly status report</p>
                <div className="flex flex-wrap gap-1.5">
                  <Badge variant={scheduleVariant(selected.latest_scope)}>Scope: {selected.latest_scope}</Badge>
                  <Badge variant={scheduleVariant(selected.latest_schedule)}>Schedule: {selected.latest_schedule}</Badge>
                  <Badge variant={scheduleVariant(selected.latest_quality)}>Quality: {selected.latest_quality}</Badge>
                  <Badge variant={scheduleVariant(selected.latest_csat)}>CSAT: {selected.latest_csat}</Badge>
                  <Badge variant={scheduleVariant(selected.latest_team)}>Team: {selected.latest_team}</Badge>
                </div>
              </div>

              <div>
                <p className="mb-1.5 text-xs font-semibold uppercase text-muted-foreground">
                  Team ({selected.total_slots} slots: {selected.billable_count} billable, {selected.shadow_count} shadow, {selected.unbilled_count} unbilled)
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {(selected.allocated_employees || []).map((e) => (
                    <Badge key={e} variant="outline">
                      {e}
                    </Badge>
                  ))}
                </div>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
