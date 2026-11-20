"use client";

import { AnimatePresence, motion } from "motion/react";
import { cloneElement, useEffect, useId, useRef, useState, type ReactElement } from "react";

/** Tooltip for one focusable child. Opens on hover (after a short delay) and on keyboard focus; Escape closes it. */
export function Tip({
  label, children, side = "bottom", block, disabled,
}: {
  label: string;
  children: ReactElement<Record<string, unknown>>;
  side?: "bottom" | "right" | "top";
  block?: boolean; // fill the parent's width (sidebar items)
  disabled?: boolean; // render the child with no tooltip (for example when the sidebar is expanded and already shows labels)
}) {
  const id = useId();
  const [show, setShow] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const on = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setShow(true), 280);
  };
  const off = () => {
    window.clearTimeout(timer.current);
    setShow(false);
  };
  if (disabled) return children;
  return (
    <span className={`tip-wrap ${block ? "block" : ""}`} onMouseEnter={on} onMouseLeave={off} onFocus={on} onBlur={off} onKeyDown={(e) => e.key === "Escape" && off()}>
      {cloneElement(children, { "aria-describedby": id })}
      <AnimatePresence>
        {show && (
          <motion.span
            id={id}
            role="tooltip"
            className={`tip ${side}`}
            initial={{ opacity: 0, y: side === "right" ? 0 : 3 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.12 }}
          >
            {label}
          </motion.span>
        )}
      </AnimatePresence>
    </span>
  );
}

export function Kbd({ children }: { children: string }) {
  return <kbd className="kbd">{children}</kbd>;
}
