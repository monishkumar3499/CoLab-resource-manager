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
import { Separator } from "@/components/ui/misc";

function availabilityVariant(p) {
  if (p === undefined || p === null) return "secondary";
  if (p >= 50) return "success";
  if (p >= 20) return "warning";
  return "danger";
}

export default function ResourcePool() {
  const { data, loading, error } = useAsync(api.people, []);
  const [search, setSearch] = useState("");
  const [geoFilter, setGeoFilter] = useState("all");
  const [coeFilter, setCoeFilter] = useState("all");
  const [selected, setSelected] = useState(null);

  const geos = useMemo(() => (data ? Array.from(new Set(data.map((p) => p.geo_cluster).filter(Boolean))) : []), [data]);
  const coes = useMemo(() => (data ? Array.from(new Set(data.map((p) => p.primary_coe).filter(Boolean))) : []), [data]);

  const filtered = useMemo(() => {
    if (!data) return [];
    return data
      .filter((p) =>
        search
          ? (p.employee_id || "").toLowerCase().includes(search.toLowerCase()) ||
            (p.job_name || "").toLowerCase().includes(search.toLowerCase())
          : true
      )
      .filter((p) => (geoFilter === "all" ? true : p.geo_cluster === geoFilter))
      .filter((p) => (coeFilter === "all" ? true : p.primary_coe === coeFilter))
      .sort((a, b) => (b.available_capacity_pct ?? 0) - (a.available_capacity_pct ?? 0));
  }, [data, search, geoFilter, coeFilter]);

  return (
    <div>
      <PageHeader title="Resource Pool" description="All active delivery employees with live capacity and skill signals." />
      <div className="space-y-4 p-6">
        <div className="flex flex-wrap items-center gap-2">
          <Input placeholder="Search employee ID or role…" value={search} onChange={(e) => setSearch(e.target.value)} className="max-w-xs" />
          <Select value={geoFilter} onValueChange={setGeoFilter}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder="Geo cluster" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All geos</SelectItem>
              {geos.map((g) => (
                <SelectItem key={g} value={g}>
                  {g}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={coeFilter} onValueChange={setCoeFilter}>
            <SelectTrigger className="w-52">
              <SelectValue placeholder="Primary CoE" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All CoEs</SelectItem>
              {coes.map((c) => (
                <SelectItem key={c} value={c}>
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="ml-auto text-xs text-muted-foreground">{filtered.length} employees</p>
        </div>

        <Card>
          <CardContent className="p-0">
            <AsyncState loading={loading} error={error}>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Employee</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>Geo</TableHead>
                    <TableHead>Primary CoE</TableHead>
                    <TableHead>Skill score</TableHead>
                    <TableHead>Availability</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filtered.map((p) => (
                    <TableRow key={p.employee_id} className="cursor-pointer" onClick={() => setSelected(p)}>
                      <TableCell className="font-medium">{p.employee_id}</TableCell>
                      <TableCell>{p.job_name}</TableCell>
                      <TableCell>{p.geo_cluster}</TableCell>
                      <TableCell>{p.primary_coe}</TableCell>
                      <TableCell>{p.avg_skill_score ? p.avg_skill_score.toFixed(1) + "/5" : "—"}</TableCell>
                      <TableCell className="w-40">
                        <div className="flex items-center gap-2">
                          <Progress value={p.available_capacity_pct ?? 0} className="h-1.5 w-20" />
                          <span className="text-xs">{(p.available_capacity_pct ?? 0).toFixed(0)}%</span>
                        </div>
                      </TableCell>
                      <TableCell>
                        {p.on_red_project ? (
                          <Badge variant="danger">On RED project</Badge>
                        ) : p.on_amber_project ? (
                          <Badge variant="warning">On AMBER project</Badge>
                        ) : p.ramp_down_flag ? (
                          <Badge variant="info">Ramping down</Badge>
                        ) : p.is_bau_only ? (
                          <Badge variant="secondary">BAU only</Badge>
                        ) : (
                          <Badge variant="success">Healthy</Badge>
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

      <Dialog open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent>
          {selected && (
            <>
              <DialogHeader>
                <DialogTitle>
                  {selected.employee_id} · {selected.job_name}
                </DialogTitle>
                <DialogDescription>
                  {selected.location} · {selected.department_name} · Tier {selected.seniority_tier} · {selected.tenure_years}y tenure
                </DialogDescription>
              </DialogHeader>

              <div className="grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">Available capacity</p>
                  <p className="text-lg font-semibold">{(selected.available_capacity_pct ?? 0).toFixed(0)}%</p>
                  <Progress value={selected.available_capacity_pct ?? 0} className="mt-1 h-1.5" />
                </div>
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">Current utilisation</p>
                  <p className="text-lg font-semibold">{(selected.effective_util_pct ?? 0).toFixed(0)}%</p>
                </div>
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">Avg skill score</p>
                  <p className="text-lg font-semibold">{(selected.avg_skill_score ?? 0).toFixed(1)} / 5</p>
                </div>
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">Avg competency score</p>
                  <p className="text-lg font-semibold">{(selected.avg_competency_score ?? 0).toFixed(1)} / 5</p>
                </div>
              </div>

              {selected.top_skills && selected.top_skills.length > 0 && (
                <div>
                  <p className="mb-1.5 text-xs font-semibold uppercase text-muted-foreground">Top skills</p>
                  <div className="flex flex-wrap gap-1.5">
                    {selected.top_skills.map((s, i) => (
                      <Badge key={i} variant="outline">
                        {s.SubSkill || s.Skill} ({s.Score}/5)
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {selected.all_coes && selected.all_coes.length > 0 && (
                <div>
                  <p className="mb-1.5 text-xs font-semibold uppercase text-muted-foreground">CoEs</p>
                  <div className="flex flex-wrap gap-1.5">
                    {selected.all_coes.map((c) => (
                      <Badge key={c} variant="secondary">
                        {c}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              <Separator />

              <div>
                <p className="mb-1.5 text-xs font-semibold uppercase text-muted-foreground">
                  Active projects ({selected.active_project_ids?.length || 0})
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {(selected.active_project_ids || []).map((pid) => (
                    <Badge key={pid} variant="outline">
                      {pid}
                    </Badge>
                  ))}
                  {(!selected.active_project_ids || selected.active_project_ids.length === 0) && (
                    <p className="text-sm text-muted-foreground">No active project allocations.</p>
                  )}
                </div>
              </div>

              <div className="flex flex-wrap gap-1.5">
                {selected.ramp_down_flag && <Badge variant="info">Ramping down · {selected.days_to_soonest_end}d to free</Badge>}
                {selected.on_red_project && <Badge variant="danger">On RED project</Badge>}
                {selected.on_amber_project && <Badge variant="warning">On AMBER project</Badge>}
                {selected.is_bau_only && <Badge variant="secondary">BAU-only (not swap eligible)</Badge>}
                {selected.swap_eligible && <Badge variant="success">Swap eligible</Badge>}
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
