"use client";

import { motion } from "motion/react";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

/** Modal built on the native <dialog>: it traps focus, makes the page inert, and closes on Escape. */
export function Dialog({
  open, title, onClose, children, footer, wide, drawer,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
  drawer?: boolean; // slide in from the right as a full-height side panel
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      d.showModal();
      d.querySelector<HTMLElement>("[data-autofocus]")?.focus(); // the first field, not the close button
    }
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className={`dialog ${wide ? "wide" : ""} ${drawer ? "drawer" : ""}`}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onMouseDown={(e) => {
        if (e.target === ref.current) onClose(); // click on the backdrop
      }}
    >
      {open && (
        <motion.div
          className={drawer ? "drawer-in" : undefined}
          initial={drawer ? { opacity: 0.6, x: 32 } : { opacity: 0, y: 8, scale: 0.985 }}
          animate={drawer ? { opacity: 1, x: 0 } : { opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.2, ease: [0.2, 0.7, 0.2, 1] }}
        >
          <div className="panel-head">
            <h2 id={titleId}>{title}</h2>
            <button className="btn sm icon" aria-label="Close dialog" onClick={onClose}><X size={14} aria-hidden /></button>
          </div>
          <div className="panel-body stack">{children}</div>
          {footer && <div className="dialog-foot">{footer}</div>}
        </motion.div>
      )}
    </dialog>
  );
}

/** A yes/no question with an optional required reason. The confirm button stays disabled until the reason is filled. */
export function ConfirmDialog({
  open, title, children, confirmLabel, tone, busy, error, reason, onReason, onConfirm, onCancel,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  tone?: "danger";
  busy?: boolean;
  error?: string;
  reason?: { label: string; value: string; required?: boolean };
  onReason?: (v: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const rid = useId();
  const blocked = busy || (reason !== undefined && reason.required !== false && reason.value.trim() === "");
  return (
    <Dialog
      open={open}
      title={title}
      onClose={() => !busy && onCancel()}
      footer={
        <>
          <button className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className={`btn ${tone === "danger" ? "danger-solid" : "primary"}`} onClick={onConfirm} disabled={blocked}>
            {busy ? "Working..." : confirmLabel}
          </button>
        </>
      }
    >
      {children}
      {reason && (
        <div className="field">
          <label htmlFor={rid}>{reason.label}</label>
          <textarea id={rid} data-autofocus value={reason.value} onChange={(e) => onReason?.(e.target.value)} />
        </div>
      )}
      {error && <div className="notice bad" role="alert"><div>{error}</div></div>}
    </Dialog>
  );
}
