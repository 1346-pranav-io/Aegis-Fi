"use client";

import { motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

/** Open/close state for a popover plus the element it hangs from. Spread `anchorProps` onto the trigger button. */
export function usePopover() {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const toggle = useCallback(() => setOpen((o) => !o), []);
  const close = useCallback(() => setOpen(false), []);
  return { open, toggle, close, anchor, anchorProps: { ref: setAnchor, "aria-haspopup": "dialog" as const, "aria-expanded": open, onClick: toggle } };
}

/**
 * A panel anchored under (or over) a trigger. Closes on Escape, on a click outside, and on resize, and puts focus
 * back on the trigger. The first focusable element inside gets focus when it opens.
 */
export function Popover({
  open, onClose, anchor, label, children, align = "end", width = 320, above = false,
}: {
  open: boolean;
  onClose: () => void;
  anchor: HTMLElement | null;
  label: string;
  children: ReactNode;
  align?: "start" | "end";
  width?: number;
  above?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // Position from the trigger's rectangle. Computed when the popover opens (the anchor cannot move while it is open).
  const pos = useMemo(() => {
    if (!open || !anchor) return { left: 0, top: 0 } as { left: number; top?: number; bottom?: number };
    const r = anchor.getBoundingClientRect();
    const w = Math.min(width, window.innerWidth - 16);
    const left = Math.max(8, Math.min(align === "end" ? r.right - w : r.left, window.innerWidth - w - 8));
    return above ? { left, bottom: window.innerHeight - r.top + 8 } : { left, top: r.bottom + 8 };
  }, [open, anchor, align, width, above]);

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      const t = e.target as Node;
      if (ref.current && !ref.current.contains(t) && !anchor?.contains(t)) onClose();
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        anchor?.focus();
      }
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", key);
    window.addEventListener("resize", onClose);
    const t = window.setTimeout(() => ref.current?.querySelector<HTMLElement>("button, a[href], input, [tabindex]:not([tabindex='-1'])")?.focus(), 30);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", key);
      window.removeEventListener("resize", onClose);
      window.clearTimeout(t);
    };
  }, [open, anchor, onClose]);

  if (!open) return null;
  return (
    <motion.div
      ref={ref}
      role="dialog"
      aria-label={label}
      className="popover"
      style={{ ...pos, width: Math.min(width, 9999), maxWidth: "calc(100vw - 16px)" }}
      initial={{ opacity: 0, y: above ? 6 : -6, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.14, ease: [0.2, 0.7, 0.2, 1] }}
    >
      {children}
    </motion.div>
  );
}
