import React from "react";

export default function PageHeader({ title, description, children }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b px-6 py-5">
      <div>
        <h1 className="text-lg font-semibold">{title}</h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
}
