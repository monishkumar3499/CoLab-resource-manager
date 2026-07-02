export function pct(v, digits = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${Number(v).toFixed(digits)}%`;
}

export function num(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toFixed(digits);
}

export function priorityVariant(p) {
  const v = String(p || "").toLowerCase();
  if (v === "gold" || v === "urgent") return "danger";
  if (v === "silver" || v === "high") return "warning";
  if (v === "bronze" || v === "medium") return "info";
  return "secondary";
}

export function healthVariant(score) {
  if (score === null || score === undefined) return "secondary";
  if (score >= 0.7) return "success";
  if (score >= 0.45) return "warning";
  return "danger";
}

export function scheduleVariant(status) {
  const s = String(status || "").toUpperCase();
  if (s === "GREEN") return "success";
  if (s === "AMBER") return "warning";
  if (s === "RED") return "danger";
  return "secondary";
}

export function planTypeLabel(type) {
  const map = {
    A_EXACT: "Exact Match",
    A_STRONG: "Strong Partial Match",
    A_TRANSFERABLE: "Transferable Skills",
    A_AVAILABILITY: "Availability-Based",
    B_SWAP: "Smart Swap",
    C_WAIT: "Soft Commit / Wait",
    E_EXTEND_START: "Extend Start Date",
    D_HIRE: "Hire Recommendation",
  };
  return map[type] || type || "—";
}

export function planTypeVariant(type) {
  if (!type) return "secondary";
  if (type.startsWith("A_")) return "success";
  if (type === "B_SWAP") return "info";
  if (type === "C_WAIT") return "warning";
  if (type === "E_EXTEND_START") return "warning";
  if (type === "D_HIRE") return "danger";
  return "secondary";
}

export function confidenceVariant(band) {
  const b = String(band || "").toUpperCase();
  if (b === "HIGH") return "success";
  if (b === "MEDIUM" || b === "MEDIUM-LOW") return "warning";
  return "danger";
}
