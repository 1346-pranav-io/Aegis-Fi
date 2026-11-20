"use client";

import { useSyncExternalStore } from "react";
import { Monitor, Moon, PanelLeft, Sun } from "lucide-react";
import { REFRESH_OPTIONS, usePref } from "@/lib/prefs";
import { sessionLeft } from "@/lib/nav";
import { applyTheme, getThemeMode, subscribeTheme, type ThemeMode } from "@/lib/theme";
import { PageHead } from "@/components/Bits";
import { useSession } from "@/components/Shell";
import { Segmented } from "@/components/ui/Controls";
import { useToast } from "@/components/ui/Toast";

/** Preferences that live in this browser only. Nothing here is sent to the API. */
export default function SettingsPage() {
  const [session] = useSession();
  const toast = useToast();
  const mode = useSyncExternalStore(subscribeTheme, getThemeMode, () => "system" as ThemeMode);
  const [refresh, setRefresh] = usePref("aegis.refresh", "10000");
  const [sidebar, setSidebar] = usePref("aegis.sidebar", "open");

  return (
    <>
      <PageHead>Preferences are stored in this browser only. Nothing here is sent to the API.</PageHead>
      <section className="panel" style={{ maxWidth: 760 }}>
        <div className="card-body">
          <div className="setting">
            <div className="grow"><b>Theme</b><span className="muted small">System follows your operating system.</span></div>
            <Segmented<ThemeMode>
              label="Theme"
              value={mode}
              onChange={(m) => { applyTheme(m); toast.ok(`Theme set to ${m}.`); }}
              options={[{ value: "system", label: "System" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]}
            />
            <span className="muted" aria-hidden>{mode === "dark" ? <Moon size={18} /> : mode === "light" ? <Sun size={18} /> : <Monitor size={18} />}</span>
          </div>
          <div className="setting">
            <div className="grow"><b>Live refresh</b><span className="muted small">How often the overview, queue, approvals, audit and system pages reload their data. Off pauses it; the Refresh button still works.</span></div>
            <Segmented label="Live refresh interval" value={refresh} onChange={(v) => { setRefresh(v); toast.ok(v === "0" ? "Live refresh is off." : "Live refresh updated."); }} options={REFRESH_OPTIONS} />
          </div>
          <div className="setting">
            <div className="grow"><b>Sidebar</b><span className="muted small">Also toggled with Ctrl B.</span></div>
            <Segmented label="Sidebar" value={sidebar} onChange={setSidebar} options={[{ value: "open", label: "Expanded" }, { value: "collapsed", label: "Collapsed" }]} />
            <span className="muted" aria-hidden><PanelLeft size={18} /></span>
          </div>
          <div className="setting">
            <div className="grow"><b>Session</b><span className="muted small">The bearer token is kept in this tab only and checked by the API on every request.</span></div>
            {session && <span className="small">{session.user} · {session.roles.join(", ") || "no roles"} · ends in {sessionLeft(session.exp)}</span>}
          </div>
        </div>
      </section>
    </>
  );
}
