import React from "react";
import { Loader2, AlertTriangle } from "lucide-react";

export default function AsyncState({ loading, error, children, label = "Loading data…" }) {
  if (loading) {
    return (
      <div className="flex h-48 flex-col items-center justify-center gap-2 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <p className="text-sm">{label}</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex h-48 flex-col items-center justify-center gap-2 text-destructive">
        <AlertTriangle className="h-5 w-5" />
        <p className="text-sm">{error.message || "Something went wrong."}</p>
      </div>
    );
  }
  return children;
}
