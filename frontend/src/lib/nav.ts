// Route metadata for the header (title and breadcrumbs). Pure: derived from the path, so no page has to register anything.

export interface Crumb {
  label: string;
  href?: string;
}

export interface RouteMeta {
  title: string;
  crumbs: Crumb[];
  section: "overview" | "cases" | "approvals" | "audit" | "system" | "settings" | "other";
}

export function routeMeta(path: string): RouteMeta {
  const p = path.replace(/\/+$/, "") || "/";
  if (p === "/") return { title: "Overview", crumbs: [{ label: "Overview" }], section: "overview" };
  if (p === "/queue") return { title: "Cases", crumbs: [{ label: "Cases" }], section: "cases" };
  if (p.startsWith("/cases/")) {
    const id = decodeURIComponent(p.slice("/cases/".length));
    return { title: "Case", crumbs: [{ label: "Cases", href: "/queue" }, { label: id }], section: "cases" };
  }
  if (p === "/approvals") return { title: "Approvals", crumbs: [{ label: "Approvals" }], section: "approvals" };
  if (p === "/audit") return { title: "Audit log", crumbs: [{ label: "Governance" }, { label: "Audit log" }], section: "audit" };
  if (p === "/system") return { title: "System", crumbs: [{ label: "Governance" }, { label: "System" }], section: "system" };
  if (p === "/settings") return { title: "Settings", crumbs: [{ label: "Settings" }], section: "settings" };
  return { title: "Aegis-FI", crumbs: [], section: "other" };
}

/** "52 min", "3 h", "expired": how long a session has left. */
export function sessionLeft(expSeconds: number, now: number = Date.now()): string {
  const s = Math.round(expSeconds - now / 1000);
  if (s <= 0) return "expired";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`;
  return `${Math.round(s / 3600)} h`;
}

export function initials(name: string): string {
  const parts = name.split(/[\s._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}
