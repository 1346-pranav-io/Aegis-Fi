// Small formatting helpers shared by pages.

export const fmtScore = (n: number): string => n.toFixed(4);

export function sentence(v: string): string {
  const s = v.toLowerCase().replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function relTime(iso: string, now: number = Date.now()): string {
  const s = Math.round((now - Date.parse(iso)) / 1000);
  if (!Number.isFinite(s)) return "";
  const a = Math.abs(s);
  const suffix = s >= 0 ? "ago" : "from now";
  if (a < 45) return s >= 0 ? "just now" : "soon";
  if (a < 3600) return `${Math.round(a / 60)} min ${suffix}`;
  if (a < 86400) return `${Math.round(a / 3600)} h ${suffix}`;
  if (a < 86400 * 45) return `${Math.round(a / 86400)} d ${suffix}`;
  return new Date(iso).toLocaleDateString();
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // The async clipboard API can be refused (embedded browsers, missing permission). Fall back to a selection copy.
    const t = document.createElement("textarea");
    t.value = text;
    t.setAttribute("readonly", "");
    t.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.appendChild(t);
    t.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      t.remove();
    }
  }
}

export function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
