import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import PageHeader from "@/components/PageHeader";
import AsyncState from "@/components/AsyncState";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/misc";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { priorityVariant } from "@/lib/format";

export default function PipelineProjects() {
  const navigate = useNavigate();
  const { data, loading, error } = useAsync(api.pipelineProjects, []);
  const [search, setSearch] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");

  const filtered = useMemo(() => {
    if (!data) return [];
    return data
      .filter((p) => (search ? (p.client || "").toLowerCase().includes(search.toLowerCase()) : true))
      .filter((p) => (priorityFilter === "all" ? true : (p.client_priority || "Other") === priorityFilter))
      .filter((p) =>
        statusFilter === "all"
          ? true
          : statusFilter === "allocated"
          ? p.allocated
          : !p.allocated
      )
      .sort((a, b) => (b.composite_priority || 0) - (a.composite_priority || 0));
  }, [data, search, priorityFilter, statusFilter]);

  const priorities = useMemo(() => {
    if (!data) return [];
    return Array.from(new Set(data.map((p) => p.client_priority || "Other")));
  }, [data]);

  return (
    <div>
      <PageHeader
        title="Pipeline Demand"
        description="All open pipeline staffing requests, sorted by composite priority."
      />
      <div className="space-y-4 p-6">
        <div className="flex flex-wrap items-center gap-2">
          <Input
            placeholder="Search by client…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="max-w-xs"
          />
          <Select value={priorityFilter} onValueChange={setPriorityFilter}>
            <SelectTrigger className="w-44">
              <SelectValue placeholder="Client priority" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All priorities</SelectItem>
              {priorities.map((p) => (
                <SelectItem key={p} value={p}>
                  {p}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-44">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="allocated">Allocated</SelectItem>
              <SelectItem value="pending">Pending</SelectItem>
            </SelectContent>
          </Select>
          <p className="ml-auto text-xs text-muted-foreground">{filtered.length} requests</p>
        </div>

        <Card>
          <CardContent className="p-0">
            <AsyncState loading={loading} error={error}>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Client</TableHead>
                    <TableHead>Cluster</TableHead>
                    <TableHead>Client Priority</TableHead>
                    <TableHead>Request Priority</TableHead>
                    <TableHead>Solution</TableHead>
                    <TableHead>Likely Start</TableHead>
                    <TableHead># Weeks</TableHead>
                    <TableHead>SOW</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filtered.map((p) => (
                    <TableRow
                      key={p.pipeline_id}
                      className="cursor-pointer"
                      onClick={() => navigate(`/pipeline/${encodeURIComponent(p.pipeline_id)}`)}
                    >
                      <TableCell className="font-medium">{p.client}</TableCell>
                      <TableCell>{p.cluster ?? "—"}</TableCell>
                      <TableCell>
                        <Badge variant={priorityVariant(p.client_priority)}>{p.client_priority || "—"}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={priorityVariant(p.priority)}>{p.priority || "—"}</Badge>
                      </TableCell>
                      <TableCell className="max-w-[180px] truncate">{p.solution || "—"}</TableCell>
                      <TableCell>{p.likely_start_str || "TBD"}</TableCell>
                      <TableCell>{p.num_weeks ?? "—"}</TableCell>
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
