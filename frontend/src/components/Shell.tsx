"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Fragment, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { motion, MotionConfig } from "motion/react";
import {
  BadgeCheck, Bell, ChevronsLeft, ChevronsRight, Fingerprint, Inbox, Keyboard, LayoutDashboard, Link2, LogOut, Menu as MenuIcon,
  Moon, Network, ScrollText, Scale, Search, Settings, ShieldCheck, Sun, X,
} from "lucide-react";
import { getStoredToken, sessionFromToken, SESSION_EVENT, setToken, signOut, UNAUTH_EVENT, type PendingApproval, type Session } from "@/lib/api";
import { relTime, sentence } from "@/lib/format";
import { initials, routeMeta, sessionLeft } from "@/lib/nav";
import { useRefreshMs, usePref, useTick } from "@/lib/prefs";
import { applyTheme, getResolved, subscribeTheme } from "@/lib/theme";
import { HeaderSlot } from "./HeaderActions";
import { Notice, Skeleton, useLoad } from "./Bits";
import { CommandPalette, type Command } from "./CommandPalette";
import { ShortcutsHelp } from "./ShortcutsHelp";
import { Popover, usePopover } from "./ui/Popover";
import { Kbd, Tip } from "./ui/Tip";
import { ToastProvider } from "./ui/Toast";

function subscribe(events: string[]) {
  return (cb: () => void) => {
    events.forEach((e) => window.addEventListener(e, cb));
    return () => events.forEach((e) => window.removeEventListener(e, cb));
  };
}
const subSession = subscribe([SESSION_EVENT, "storage"]);

/** The signed-in session, read from the stored token. `ready` is false on the server and during hydration. */
export function useSession(): [Session | null, boolean] {
  const token = useSyncExternalStore(subSession, () => getStoredToken() ?? "", () => null);
  const session = useMemo(() => sessionFromToken(token || null), [token]);
  return [session, token !== null];
}

function SignIn({ expired }: { expired: boolean }) {
  const [token, setTok] = useState("");
  const [err, setErr] = useState("");
  return (
    <div className="sign">
      <section className="sign-art">
        <div className="brand">
          <span className="brand-mark"><ShieldCheck size={16} aria-hidden /></span>
          Aegis-FI
        </div>
        <div>
          <h1>Every finding traces back to its evidence.</h1>
          <ul>
            <li><Link2 size={18} aria-hidden /><span>Claims cite evidence ids. A claim that cannot be verified never reaches a report.</span></li>
            <li><Scale size={18} aria-hidden /><span>Scores rank cases. They are not probabilities, and the interface says so.</span></li>
            <li><Fingerprint size={18} aria-hidden /><span>Closing a case or exporting feedback needs a stored decision from a second person.</span></li>
          </ul>
        </div>
        <span className="muted small">Local research system. Not connected to any bank or regulator.</span>
      </section>
      <section className="sign-form">
        <form
          className="panel"
          onSubmit={(e) => {
            e.preventDefault();
            if (!setToken(token)) setErr("That token could not be read. Check that you pasted the whole string.");
          }}
        >
          <div className="panel-head"><h2>Sign in</h2></div>
          <div className="panel-body stack">
            {expired && <Notice tone="info">Your session ended because the token expired or was refused. Paste a new one to continue.</Notice>}
            <div className="field">
              <label htmlFor="token">Bearer token</label>
              <textarea id="token" value={token} onChange={(e) => setTok(e.target.value)} spellCheck={false} autoFocus />
              <span className="help">
                For local use, create one with <code>uv run python -m aegis.cli dev-token --user you --roles analyst approver</code>.
                It stays in this tab, and the API checks it on every request.
              </span>
            </div>
            {err && <Notice tone="bad">{err}</Notice>}
            <div><button className="btn primary" type="submit" disabled={!token.trim()}>Sign in</button></div>
          </div>
        </form>
      </section>
    </div>
  );
}

function isTyping(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

function Avatar({ name }: { name: string }) {
  return <span className="avatar" aria-hidden>{initials(name)}</span>;
}

export function Shell({ children }: { children: ReactNode }) {
  const [session, ready] = useSession();
  const [expired, setExpired] = useState(false);
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const [collapsedPref, setCollapsedPref] = usePref("aegis.sidebar", "open");
  const collapsed = collapsedPref === "collapsed";
  const theme = useSyncExternalStore(subscribeTheme, getResolved, () => "light" as const);
  const path = usePathname();
  const router = useRouter();
  const chord = useRef<number | null>(null);
  const meta = routeMeta(path);
  const [refreshMs] = useRefreshMs();
  const tick = useTick(refreshMs);
  const bell = usePopover();
  const acctSide = usePopover(); // account menu opened from the sidebar footer (opens upwards)
  const acctTop = usePopover(); // the same menu opened from the header avatar

  useEffect(() => {
    const e = () => setExpired(true);
    const p = () => setPalette(true); // pages can open the palette (quick actions) without owning its state
    window.addEventListener(UNAUTH_EVENT, e);
    window.addEventListener("aegis:palette", p);
    return () => {
      window.removeEventListener(UNAUTH_EVENT, e);
      window.removeEventListener("aegis:palette", p);
    };
  }, []);
  // Leaving the page closes the phone navigation. (Derived from the path, not an effect.)
  const [navPath, setNavPath] = useState(path);
  if (navPath !== path) {
    setNavPath(path);
    setMobileNav(false);
  }

  const can = useCallback((...r: string[]) => !!session && session.roles.some((x) => r.includes(x)), [session]);
  const canApprovals = can("approver", "analyst");
  const approvals = useLoad<PendingApproval[]>(session && canApprovals ? "/approvals/pending" : null, tick);
  const mine = session?.user ?? "";
  const waiting = (approvals.data ?? []).filter((a) => a.requested_by !== mine);
  const needsMe = can("approver") ? waiting.length : 0;

  const items = useMemo(
    () => [
      { group: "Workspace", href: "/", label: "Overview", icon: LayoutDashboard, show: true, active: path === "/", key: "o" },
      { group: "Workspace", href: "/queue", label: "Cases", icon: Inbox, show: true, active: path.startsWith("/queue") || path.startsWith("/cases"), key: "q" },
      { group: "Workspace", href: "/approvals", label: "Approvals", icon: BadgeCheck, show: canApprovals, active: path === "/approvals", key: "a", badge: needsMe },
      { group: "Governance", href: "/audit", label: "Audit log", icon: ScrollText, show: can("auditor", "admin"), active: path === "/audit", key: "l" },
      { group: "Governance", href: "/system", label: "System", icon: Network, show: true, active: path === "/system", key: "s" },
    ].filter((i) => i.show),
    [path, can, canApprovals, needsMe],
  );

  const logout = useCallback(() => {
    signOut();
    router.push("/");
  }, [router]);

  const commands = useMemo<Command[]>(
    () => [
      ...items.map((i) => ({ id: `go-${i.href}`, label: i.label, group: "Go to" as const, icon: <i.icon size={16} aria-hidden />, hint: `g ${i.key}`, run: () => router.push(i.href) })),
      { id: "go-settings", label: "Settings", group: "Go to" as const, icon: <Settings size={16} aria-hidden />, hint: "g t", run: () => router.push("/settings") },
      { id: "theme", label: "Toggle light or dark theme", group: "Do" as const, keywords: "appearance", run: () => applyTheme(getResolved() === "dark" ? "light" : "dark") },
      { id: "sidebar", label: collapsed ? "Expand the sidebar" : "Collapse the sidebar", group: "Do" as const, hint: "Ctrl B", run: () => setCollapsedPref(collapsed ? "open" : "collapsed") },
      { id: "keys", label: "Show keyboard shortcuts", group: "Do" as const, hint: "?", run: () => setHelp(true) },
      { id: "out", label: "Sign out", group: "Do" as const, run: logout },
    ],
    [items, router, logout, collapsed, setCollapsedPref],
  );

  useEffect(() => {
    if (!session) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((o) => !o);
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        setCollapsedPref(collapsed ? "open" : "collapsed");
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target) || document.querySelector("dialog[open]")) return;
      if (e.key === "/") {
        const el = document.querySelector<HTMLInputElement>("[data-search]");
        if (el) { e.preventDefault(); el.focus(); el.select(); }
        else { e.preventDefault(); setPalette(true); }
      } else if (e.key === "?") {
        e.preventDefault();
        setHelp(true);
      } else if (chord.current !== null) {
        window.clearTimeout(chord.current);
        chord.current = null;
        const k = e.key.toLowerCase();
        const to = k === "t" ? { href: "/settings" } : items.find((i) => i.key === k);
        if (to) { e.preventDefault(); router.push(to.href); }
      } else if (e.key === "g") {
        chord.current = window.setTimeout(() => (chord.current = null), 1000);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [session, items, router, collapsed, setCollapsedPref]);

  if (!ready) return null;
  if (!session) return <SignIn expired={expired} />;

  const groups = [...new Set(items.map((i) => i.group))];
  const events = (approvals.data ?? []).slice(0, 5);

  return (
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <a className="skip" href="#main">Skip to content</a>
        <div className={`app ${mobileNav ? "nav-open" : ""}`}>
          {mobileNav && <button className="scrim" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}
          <motion.aside className="sb" data-collapsed={collapsed} initial={false} animate={{ width: collapsed ? 68 : 244 }} transition={{ duration: 0.2, ease: [0.2, 0.7, 0.2, 1] }}>
            <div className="sb-brand">
              <span className="brand-mark"><ShieldCheck size={18} aria-hidden /></span>
              <span className="sb-label brand-name">Aegis-FI</span>
              <button className="btn sm icon only-mobile" aria-label="Close navigation" onClick={() => setMobileNav(false)}><X size={16} aria-hidden /></button>
            </div>
            <nav className="sb-nav" aria-label="Main">
              {groups.map((g) => (
                <Fragment key={g}>
                  <div className="sb-group sb-label">{g}</div>
                  {items.filter((i) => i.group === g).map((i) => (
                    <Tip key={i.href} label={i.label} side="right" block disabled={!collapsed}>
                      <Link href={i.href} className="sb-item" aria-current={i.active ? "page" : undefined} aria-label={collapsed ? i.label : undefined}>
                        <i.icon size={18} aria-hidden />
                        <span className="sb-label">{i.label}</span>
                        {!!i.badge && <span className="sb-badge num" aria-label={`${i.badge} waiting`}>{i.badge}</span>}
                      </Link>
                    </Tip>
                  ))}
                </Fragment>
              ))}
            </nav>
            <div className="sb-foot">
              <Tip label="Settings" side="right" block disabled={!collapsed}>
                <Link href="/settings" className="sb-item" aria-current={path === "/settings" ? "page" : undefined} aria-label={collapsed ? "Settings" : undefined}>
                  <Settings size={18} aria-hidden /><span className="sb-label">Settings</span>
                </Link>
              </Tip>
              <Tip label={`${session.user} (${session.roles.join(", ") || "no roles"})`} side="right" block disabled={!collapsed}>
                <button className="sb-user" {...acctSide.anchorProps} aria-label={`Account menu for ${session.user}`}>
                  <Avatar name={session.user} />
                  <span className="sb-label me"><b>{session.user}</b><span className="muted small">{session.roles.join(", ") || "no roles"}</span></span>
                </button>
              </Tip>
              <Tip label="Expand sidebar (Ctrl B)" side="right" block disabled={!collapsed}>
                <button className="sb-item sb-collapse" onClick={() => setCollapsedPref(collapsed ? "open" : "collapsed")} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed}>
                  {collapsed ? <ChevronsRight size={18} aria-hidden /> : <ChevronsLeft size={18} aria-hidden />}
                  <span className="sb-label">Collapse</span>
                </button>
              </Tip>
            </div>
          </motion.aside>

          <div className="main">
            <header className="topbar">
              <button className="btn icon only-mobile" aria-label="Open navigation" onClick={() => setMobileNav(true)}><MenuIcon size={18} aria-hidden /></button>
              <div className="titleblock">
                {meta.crumbs.length > 1 && (
                  <nav aria-label="Breadcrumb" className="crumbs">
                    {meta.crumbs.map((c, i) => (
                      <span key={c.label}>
                        {i > 0 && <span aria-hidden className="sep">/</span>}
                        {c.href ? <Link href={c.href}>{c.label}</Link> : <span aria-current={i === meta.crumbs.length - 1 ? "page" : undefined} className={i === meta.crumbs.length - 1 ? "mono here" : ""}>{c.label}</span>}
                      </span>
                    ))}
                  </nav>
                )}
                <h1 className="page-title">{meta.title}</h1>
              </div>
              <div className="topbar-actions" ref={setSlot} />
              <button className="gsearch" onClick={() => setPalette(true)} aria-label="Search cases and pages">
                <Search size={15} aria-hidden /> <span className="grow">Search cases, pages, actions</span> <Kbd>Ctrl K</Kbd>
              </button>
              <Tip label="Notifications">
                <button className="btn icon bell" {...bell.anchorProps} aria-label={needsMe ? `Notifications, ${needsMe} need your decision` : "Notifications"}>
                  <Bell size={17} aria-hidden />{needsMe > 0 && <span className="bell-dot" aria-hidden />}
                </button>
              </Tip>
              <Popover open={bell.open} onClose={bell.close} anchor={bell.anchor} label="Notifications" width={340}>
                <div className="pop-head"><b>Notifications</b>{canApprovals && <span className="muted small">{waiting.length} waiting for a decision</span>}</div>
                {!canApprovals ? (
                  <div className="empty"><b>Nothing to show</b>Your roles do not receive approval notifications.</div>
                ) : approvals.loading && !approvals.data ? <Skeleton rows={3} />
                : approvals.error ? <div className="pop-body"><Notice tone="bad">{approvals.error}</Notice></div>
                : events.length === 0 ? <div className="empty"><b>All clear</b>No approvals are waiting.</div>
                : (
                  <ul className="pop-list">
                    {events.map((a) => (
                      <li key={a.request_id}>
                        <Link href="/approvals" onClick={bell.close}>
                          <BadgeCheck size={16} aria-hidden className="muted" />
                          <span className="grow"><b>{sentence(a.action_type)}</b><br /><span className="muted small">{a.requested_by === mine ? "You requested this" : `Requested by ${a.requested_by}`} · {relTime(a.requested_at)}</span></span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="pop-foot"><Link href="/approvals" onClick={bell.close}>Open approvals</Link></div>
              </Popover>
              <button className="avatar-btn" {...acctTop.anchorProps} aria-label={`Account menu for ${session.user}`}><Avatar name={session.user} /></button>
              <AccountPopover pop={acctSide} session={session} theme={theme} onShortcuts={() => setHelp(true)} onLogout={logout} above align="start" />
              <AccountPopover pop={acctTop} session={session} theme={theme} onShortcuts={() => setHelp(true)} onLogout={logout} />
            </header>
            <main className="content" id="main" tabIndex={-1}>
              <HeaderSlot.Provider value={slot}>
                <Suspense fallback={<Skeleton rows={5} />}>{children}</Suspense>
              </HeaderSlot.Provider>
            </main>
          </div>
        </div>
        <CommandPalette open={palette} onClose={() => setPalette(false)} commands={commands} />
        <ShortcutsHelp open={help} onClose={() => setHelp(false)} />
      </ToastProvider>
    </MotionConfig>
  );
}

function AccountPopover({
  pop, session, theme, onShortcuts, onLogout, above, align,
}: {
  pop: ReturnType<typeof usePopover>;
  session: Session;
  theme: "light" | "dark";
  onShortcuts: () => void;
  onLogout: () => void;
  above?: boolean;
  align?: "start" | "end";
}) {
  return (
    <Popover open={pop.open} onClose={pop.close} anchor={pop.anchor} label="Account" width={264} above={above} align={align}>
      <div className="pop-head">
        <Avatar name={session.user} />
        <span className="grow"><b>{session.user}</b><br /><span className="muted small">{session.roles.join(", ") || "no roles"}</span></span>
      </div>
      <div className="pop-body muted small">Session ends in {sessionLeft(session.exp)}.</div>
      <ul className="pop-menu">
        <li><Link href="/settings" onClick={pop.close}><Settings size={16} aria-hidden /> Settings</Link></li>
        <li><button onClick={() => { pop.close(); onShortcuts(); }}><Keyboard size={16} aria-hidden /> Keyboard shortcuts</button></li>
        <li><button onClick={() => applyTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? <Sun size={16} aria-hidden /> : <Moon size={16} aria-hidden />} {theme === "dark" ? "Light theme" : "Dark theme"}</button></li>
        <li><button onClick={() => { pop.close(); onLogout(); }}><LogOut size={16} aria-hidden /> Sign out</button></li>
      </ul>
    </Popover>
  );
}
