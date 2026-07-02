import React, { useMemo } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RTooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import PageHeader from "@/components/PageHeader";
import AsyncState from "@/components/AsyncState";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";

function bucketCapacity(people) {
  const buckets = { "0-24%": 0, "25-49%": 0, "50-74%": 0, "75-100%": 0 };
  people.forEach((p) => {
    const v = p.available_capacity_pct ?? 0;
    if (v < 25) buckets["0-24%"] += 1;
    else if (v < 50) buckets["25-49%"] += 1;
    else if (v < 75) buckets["50-74%"] += 1;
    else buckets["75-100%"] += 1;
  });
  return Object.entries(buckets).map(([name, value]) => ({ name, value }));
}

function headcountByGeo(people) {
  const map = {};
  people.forEach((p) => {
    const geo = p.geo_cluster || "Unknown";
    map[geo] = (map[geo] || 0) + 1;
  });
  return Object.entries(map).map(([name, value]) => ({ name, value }));
}

function skillByCoe(people) {
  const sums = {};
  const counts = {};
  people.forEach((p) => {
    if (!p.has_skill_data) return;
    const coe = p.primary_coe || "Unknown";
    sums[coe] = (sums[coe] || 0) + (p.avg_skill_score || 0);
    counts[coe] = (counts[coe] || 0) + 1;
  });
  return Object.keys(sums).map((coe) => ({
    name: coe,
    value: Number((sums[coe] / counts[coe]).toFixed(2)),
  }));
}

function hireDemand(pipeline) {
  const map = {};
  pipeline.forEach((p) => {
    // not directly available pre-allocation; approximate demand by role count via solution
    const key = p.client_priority || "Other";
    map[key] = (map[key] || 0) + 1;
  });
  return Object.entries(map).map(([name, value]) => ({ name, value }));
}

export default function Analytics() {
  const people = useAsync(api.people, []);
  const pipeline = useAsync(api.pipelineProjects, []);
  const config = useAsync(api.config, []);

  const capacityData = useMemo(() => (people.data ? bucketCapacity(people.data) : []), [people.data]);
  const geoData = useMemo(() => (people.data ? headcountByGeo(people.data) : []), [people.data]);
  const skillData = useMemo(() => (people.data ? skillByCoe(people.data) : []), [people.data]);
  const demandData = useMemo(() => (pipeline.data ? hireDemand(pipeline.data) : []), [pipeline.data]);

  const weightsData = useMemo(() => {
    if (!config.data?.weights) return [];
    return Object.entries(config.data.weights)
      .filter(([k]) => !k.includes("boost") && !k.includes("penalty"))
      .map(([k, v]) => ({ name: k.replace(/_/g, " "), value: v }))
      .sort((a, b) => b.value - a.value);
  }, [config.data]);

  return (
    <div>
      <PageHeader title="Analytics" description="Aggregate workforce, demand, and scoring transparency." />
      <div className="grid gap-4 p-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Capacity distribution</CardTitle>
            <CardDescription>Employees grouped by available capacity band</CardDescription>
          </CardHeader>
          <CardContent>
            <AsyncState loading={people.loading} error={people.error}>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={capacityData}>
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
            <CardTitle>Headcount by geo cluster</CardTitle>
            <CardDescription>Active delivery employees per region</CardDescription>
          </CardHeader>
          <CardContent>
            <AsyncState loading={people.loading} error={people.error}>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={geoData}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="name" fontSize={12} />
                  <YAxis fontSize={12} allowDecimals={false} />
                  <RTooltip />
                  <Bar dataKey="value" fill="#475569" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </AsyncState>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Average skill score by CoE</CardTitle>
            <CardDescription>Out of 5 — employees with skill data only</CardDescription>
          </CardHeader>
          <CardContent>
            <AsyncState loading={people.loading} error={people.error}>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={skillData} layout="vertical" margin={{ left: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" domain={[0, 5]} fontSize={12} />
                  <YAxis type="category" dataKey="name" width={150} fontSize={11} />
                  <RTooltip />
                  <Bar dataKey="value" fill="#0f172a" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </AsyncState>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Ranking weight model</CardTitle>
            <CardDescription>How the engine scores candidates (config.py weights)</CardDescription>
          </CardHeader>
          <CardContent>
            <AsyncState loading={config.loading} error={config.error}>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={weightsData} layout="vertical" margin={{ left: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" fontSize={12} />
                  <YAxis type="category" dataKey="name" width={150} fontSize={11} />
                  <RTooltip />
                  <Bar dataKey="value" fill="#94a3b8" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </AsyncState>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Pipeline demand by client priority</CardTitle>
            <CardDescription>Number of open pipeline requests per tier</CardDescription>
          </CardHeader>
          <CardContent>
            <AsyncState loading={pipeline.loading} error={pipeline.error}>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={demandData}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="name" fontSize={12} />
                  <YAxis fontSize={12} allowDecimals={false} />
                  <RTooltip />
                  <Legend />
                  <Bar dataKey="value" name="Pipeline requests" fill="#0f172a" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </AsyncState>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
