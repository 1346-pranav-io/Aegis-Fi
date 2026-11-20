"use client";

import { motion } from "motion/react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

export interface MenuItem {
  label: string;
  icon?: ReactNode;
  hint?: string;
  disabled?: boolean;
  tone?: "danger";
  onSelect: () => void;
}

interface MenuState {
  x: number;
  y: number;
  items: MenuItem[];
  returnTo: HTMLElement | null;
}

/** Opens a menu at the pointer (right-click) or under a trigger element (keyboard or the "more" button). */
export function useMenu() {
  const [state, setState] = useState<MenuState | null>(null);
  const openAt = useCallback((e: { clientX: number; clientY: number }, items: MenuItem[]) => {
    setState({ x: e.clientX, y: e.clientY, items, returnTo: document.activeElement as HTMLElement | null });
  }, []);
  const openFor = useCallback((el: HTMLElement, items: MenuItem[]) => {
    const r = el.getBoundingClientRect();
    setState({ x: r.right, y: r.bottom + 4, items, returnTo: el });
  }, []);
  const close = useCallback(() => setState(null), []);
  return { state, openAt, openFor, close };
}

export function Menu({ state, onClose }: { state: MenuState | null; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: 0, top: 0 });
  const [active, setActive] = useState(0);

  // Keep the menu inside the viewport. A trigger at the right edge opens leftwards.
  useLayoutEffect(() => {
    if (!state || !ref.current) return;
    const { width, height } = ref.current.getBoundingClientRect();
    const left = Math.max(8, Math.min(state.x, window.innerWidth - width - 8));
    const top = Math.max(8, Math.min(state.y, window.innerHeight - height - 8));
    setPos({ left, top });
    setActive(Math.max(0, state.items.findIndex((i) => !i.disabled)));
    ref.current.focus();
  }, [state]);

  const close = useCallback(() => {
    state?.returnTo?.focus();
    onClose();
  }, [state, onClose]);

  useEffect(() => {
    if (!state) return;
    const away = (e: Event) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const dismiss = () => onClose();
    document.addEventListener("pointerdown", away);
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    return () => {
      document.removeEventListener("pointerdown", away);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [state, onClose]);

  if (!state) return null;
  const items = state.items;
  const step = (d: 1 | -1) => {
    let i = active;
    for (let n = 0; n < items.length; n++) {
      i = (i + d + items.length) % items.length;
      if (!items[i].disabled) break;
    }
    setActive(i);
  };
  const choose = (i: MenuItem) => {
    if (i.disabled) return;
    onClose();
    state.returnTo?.focus();
    i.onSelect();
  };

  return (
    <motion.div
      ref={ref}
      role="menu"
      tabIndex={-1}
      className="menu"
      style={{ left: pos.left, top: pos.top }}
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.1 }}
      onKeyDown={(e) => {
        e.stopPropagation(); // the page behind has its own key handling (j/k/Enter); the menu owns the keyboard while open
        if (e.key === "Escape") { e.preventDefault(); close(); }
        else if (e.key === "ArrowDown") { e.preventDefault(); step(1); }
        else if (e.key === "ArrowUp") { e.preventDefault(); step(-1); }
        else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(items[active]); }
        else if (e.key === "Tab") { e.preventDefault(); close(); }
      }}
    >
      {items.map((it, i) => (
        <button
          key={it.label}
          role="menuitem"
          className={`menu-item ${i === active ? "active" : ""} ${it.tone ?? ""}`}
          disabled={it.disabled}
          onMouseEnter={() => !it.disabled && setActive(i)}
          onClick={() => choose(it)}
          tabIndex={-1}
        >
          {it.icon}
          <span className="grow">{it.label}</span>
          {it.hint && <span className="muted small">{it.hint}</span>}
        </button>
      ))}
    </motion.div>
  );
}
