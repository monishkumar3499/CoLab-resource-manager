import React, { useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RTooltip,
  ResponsiveContainer,
} from "recharts";
import { ArrowLeft, RefreshCcw, CheckCircle2, Undo2, User, Clock, TrendingUp } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import PageHeader from "@/components/PageHeader";
import AsyncState from "@/components/AsyncState";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/misc";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from "@/components/ui/misc";
import {
  priorityVariant,
  planTypeLabel,
  planTypeVariant,
  confidenceVariant,
} from "@/lib/format";

function ScoreBars({ breakdown }) {
  if (!breakdown || Object.keys(breakdown).length === 0) {
    return <p className="text-xs text-muted-foreground">No score breakdown available for this plan type.</p>;
  }
  const keys = [
    "semantic_similarity",
    "skill_confidence",
    "coe_match",
    "location",
    "capability_score",
    "operational_score",
  ];
  const data = keys
    .filter((k) => k in breakdown)
    .map((k) => ({ name: k.replace(/_/g, " "), value: breakdown[k] }));
  if (!data.length) return null;
  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart data={data} layout="vertical" margin={{ left: 10, right: 20 }}>
        <CartesianGrid strokeDasharray="3 3" horizontal={false} />
        <XAxis type="number" domain={[0, 1]} fontSize={11} />
        <YAxis type="category" dataKey="name" width={120} fontSize={11} />
        <RTooltip />
        <Bar dataKey="value" fill="#0f172a" radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function OptionCard({ option, highlighted }) {
  if (!option) return null;
  return (
    <div className={`rounded-md border p-3 ${highlighted ? "border-primary bg-muted/40" : ""}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge variant={planTypeVariant(option.plan_type)}>{planTypeLabel(option.plan_type)}</Badge>
          <Badge variant={confidenceVariant(option.confidence_band)}>{option.confidence_band} confidence</Badge>
          {option.implementation_complexity && (
            <Badge variant="outline">{option.implementation_complexity} complexity</Badge>
          )}
        </div>
        <span className="text-sm font-semibold">{(option.composite_score * 100).toFixed(0)}% match</span>
      </div>

      <div className="mt-2 flex items-center gap-2 text-sm">
        <User className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium">{option.recommended_employee_id || "External Hire"}</span>
        {option.job_name && <span className="text-muted-foreground">· {option.job_name}</span>}
        {option.location && <span className="text-muted-foreground">· {option.location}</span>}
      </div>

      {option.estimated_delay_days > 0 && (
        <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Clock className="h-3.5 w-3.5" />
          Estimated delay: {option.estimated_delay_days} day(s)
          {option.expected_start_date ? ` · expected start ${option.expected_start_date}` : ""}
        </div>
      )}

      {option.extend_start_reason && (
        <p className="mt-2 text-xs text-muted-foreground">{option.extend_start_reason}</p>
      )}

      {option.business_impact_summary && (
        <p className="mt-2 text-sm leading-relaxed">{option.business_impact_summary}</p>
      )}

      {option.swap_chain_summary && option.swap_chain_summary.length > 0 && (
        <div className="mt-2 space-y-1 rounded-md bg-muted/50 p-2">
          <p className="text-xs font-medium text-muted-foreground">Swap chain</p>
          {option.swap_chain_summary.map((step, i) => (
            <p key={i} className="text-xs">
              {step}
            </p>
          ))}
        </div>
      )}

      {option.revenue_contribution_weekly > 0 && (
        <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
          <TrendingUp className="h-3.5 w-3.5" />
          Weekly revenue contribution: £{Number(option.revenue_contribution_weekly).toLocaleString()}
        </div>
      )}

      <Separator className="my-3" />
      <p className="mb-1 text-xs font-medium text-muted-foreground">Why this score</p>
      <ScoreBars breakdown={option.score_breakdown} />
    </div>
  );
}

function RolePlanCard({ rp }) {
  const opt = rp.recommended_option;
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle>
              {rp.role_name} <span className="font-normal text-muted-foreground">({rp.required_role}, {rp.required_pct}%)</span>
            </CardTitle>
            <CardDescription>
              {rp.gap_detected ? rp.gap_reason || "Gap detected" : "Coverage confirmed"}
            </CardDescription>
          </div>
          {rp.hire_signal && <Badge variant="danger">Hire signal · {rp.hire_urgency}</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {opt ? (
          <OptionCard option={opt} highlighted />
        ) : (
          <p className="text-sm text-muted-foreground">No staffing option could be generated for this role.</p>
        )}

        {rp.all_options && rp.all_options.length > 1 && (
          <Accordion type="single" collapsible>
            <AccordionItem value="alts">
              <AccordionTrigger className="text-xs text-muted-foreground">
                View {rp.all_options.length - 1} alternative option(s)
              </AccordionTrigger>
              <AccordionContent className="space-y-2">
                {rp.all_options
                  .filter((o) => o !== opt)
                  .map((o, i) => (
                    <OptionCard key={i} option={o} />
                  ))}
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        )}
      </CardContent>
    </Card>
  );
}

export default function RecommendationDetail() {
  const { pipelineId } = useParams();
  const { data, loading, error, reload } = useAsync(() => api.preview(pipelineId), [pipelineId]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  async function handleAllocate() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.allocate(pipelineId);
      setMsg("Allocations committed successfully.");
      reload();
    } catch (e) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleUndo() {
    setBusy(true);
    setMsg(null);
    try {
      await api.undoAllocate(pipelineId);
      setMsg("Allocations reverted.");
      reload();
    } catch (e) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  const plan = data?.project_plan;
  const llm = data?.llm_output;

  return (
    <div>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <Link to="/pipeline" className="text-muted-foreground hover:text-foreground">
              <ArrowLeft className="h-4 w-4" />
            </Link>
            {plan?.client || pipelineId}
          </span>
        }
        description={pipelineId}
      >
        <Button variant="outline" size="sm" onClick={reload} disabled={busy || loading}>
          <RefreshCcw className="mr-1.5 h-4 w-4" />
          Re-run preview
        </Button>
        <Button variant="outline" size="sm" onClick={handleUndo} disabled={busy}>
          <Undo2 className="mr-1.5 h-4 w-4" />
          Undo allocation
        </Button>
        <Button size="sm" onClick={handleAllocate} disabled={busy}>
          <CheckCircle2 className="mr-1.5 h-4 w-4" />
          Commit allocation
        </Button>
      </PageHeader>

      <div className="space-y-6 p-6">
        {msg && <div className="rounded-md border bg-muted/40 px-3 py-2 text-sm">{msg}</div>}

        <AsyncState loading={loading} error={error} label="Running 8-stage recommendation pipeline…">
          {plan && (
            <>
              <div className="grid gap-4 md:grid-cols-4">
                <Card className="md:col-span-2">
                  <CardHeader>
                    <CardTitle>Coverage</CardTitle>
                    <CardDescription>{plan.total_roles} role slot(s) requested</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="mb-2 flex items-center justify-between text-sm">
                      <span>{plan.coverage_pct.toFixed(0)}% covered</span>
                      <span className="text-muted-foreground">Confidence {(plan.composite_confidence * 100).toFixed(0)}%</span>
                    </div>
                    <Progress value={plan.coverage_pct} />
                    <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
                      <Badge variant="success">{plan.roles_filled_immediate} immediate</Badge>
                      <Badge variant="info">{plan.roles_filled_via_swap} swap</Badge>
                      <Badge variant="warning">{plan.roles_filled_via_wait} wait</Badge>
                      <Badge variant="danger">{plan.roles_needing_hire} hire</Badge>
                      {plan.roles_unfilled > 0 && <Badge variant="secondary">{plan.roles_unfilled} unfilled</Badge>}
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>Client context</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-1.5 text-sm">
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">Client priority</span>
                      <Badge variant={priorityVariant(plan.client_priority)}>{plan.client_priority}</Badge>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">Request priority</span>
                      <Badge variant={priorityVariant(plan.request_priority)}>{plan.request_priority}</Badge>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">SOW signed</span>
                      <span>{plan.sow_signed ? "Yes" : "No"}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">Likely start</span>
                      <span>{plan.likely_start_date || "TBD"}</span>
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>Plan complexity</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-1.5 text-sm">
                    <Badge variant={plan.overall_complexity === "CRITICAL" ? "danger" : plan.overall_complexity === "HIGH" ? "warning" : "success"}>
                      {plan.overall_complexity}
                    </Badge>
                    {plan.extend_start_date_recommended && (
                      <p className="text-xs text-muted-foreground">
                        Start date extension suggested → {plan.recommended_start_date}
                      </p>
                    )}
                  </CardContent>
                </Card>
              </div>

              <Tabs defaultValue="roles">
                <TabsList>
                  <TabsTrigger value="roles">Role Recommendations</TabsTrigger>
                  <TabsTrigger value="summary">Executive Summary</TabsTrigger>
                  <TabsTrigger value="sequence">Implementation Plan</TabsTrigger>
                  <TabsTrigger value="risks">Risks &amp; Hiring</TabsTrigger>
                </TabsList>

                <TabsContent value="roles" className="space-y-4">
                  {plan.role_plans.map((rp) => (
                    <RolePlanCard key={rp.role_id} rp={rp} />
                  ))}
                </TabsContent>

                <TabsContent value="summary">
                  <Card>
                    <CardContent className="space-y-4 p-4 text-sm leading-relaxed">
                      <section>
                        <h3 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Executive summary</h3>
                        <p>{llm?.executive_summary}</p>
                      </section>
                      <section>
                        <h3 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Recommendation rationale</h3>
                        <p className="whitespace-pre-line">{llm?.recommendation_rationale}</p>
                      </section>
                      <section>
                        <h3 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Alternatives considered</h3>
                        <p className="whitespace-pre-line">{llm?.alternative_rejection_summary}</p>
                      </section>
                      <section>
                        <h3 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Business impact</h3>
                        <p className="whitespace-pre-line">{llm?.business_impact_narrative}</p>
                      </section>
                      <section>
                        <h3 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Swap chains</h3>
                        <p className="whitespace-pre-line">{llm?.swap_chain_explanation}</p>
                      </section>
                    </CardContent>
                  </Card>
                </TabsContent>

                <TabsContent value="sequence">
                  <Card>
                    <CardHeader>
                      <CardTitle>RM action sequence</CardTitle>
                      <CardDescription>Ordered steps for the resource manager</CardDescription>
                    </CardHeader>
                    <CardContent>
                      <ol className="space-y-2 text-sm">
                        {plan.implementation_sequence.map((step, i) => (
                          <li key={i} className="rounded-md border px-3 py-2">
                            {step}
                          </li>
                        ))}
                      </ol>
                    </CardContent>
                  </Card>
                </TabsContent>

                <TabsContent value="risks" className="space-y-4">
                  <Card>
                    <CardHeader>
                      <CardTitle>Risks &amp; assumptions</CardTitle>
                    </CardHeader>
                    <CardContent className="whitespace-pre-line text-sm leading-relaxed">
                      {llm?.risks_and_assumptions}
                    </CardContent>
                  </Card>
                  {Object.keys(plan.hire_headcount_by_role || {}).length > 0 && (
                    <Card>
                      <CardHeader>
                        <CardTitle>Hiring requirements</CardTitle>
                        <CardDescription>{llm?.hiring_justification}</CardDescription>
                      </CardHeader>
                      <CardContent className="flex flex-wrap gap-2">
                        {Object.entries(plan.hire_headcount_by_role).map(([role, count]) => (
                          <Badge key={role} variant="danger">
                            {role}: {count} hire(s)
                          </Badge>
                        ))}
                      </CardContent>
                    </Card>
                  )}
                  <Card>
                    <CardHeader>
                      <CardTitle>RM action notes</CardTitle>
                    </CardHeader>
                    <CardContent className="whitespace-pre-line text-sm leading-relaxed">
                      {llm?.rm_action_notes}
                    </CardContent>
                  </Card>
                </TabsContent>
              </Tabs>
            </>
          )}
        </AsyncState>
      </div>
    </div>
  );
}
