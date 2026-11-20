"use client";

import { AnimatePresence, motion } from "motion/react";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { CheckCircle, Info, X, XCircle } from "lucide-react";

type Tone = "ok" | "bad" | "info";
interface ToastItem {
  id: number;
  tone: Tone;
  text: string;
}
interface Api {
  ok: (text: string) => void;
  error: (text: string) => void;
  info: (text: string) => void;
}

const Ctx = createContext<Api | null>(null);

export function useToast(): Api {
  const v = useContext(Ctx);
  if (!v) throw new Error("useToast must be used inside <ToastProvider>");
  return v;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);
  const dismiss = useCallback((id: number) => setItems((l) => l.filter((t) => t.id !== id)), []);
  const push = useCallback(
    (tone: Tone, text: string) => {
      const id = next.current++;
      setItems((l) => [...l.slice(-3), { id, tone, text }]);
      window.setTimeout(() => dismiss(id), tone === "bad" ? 9000 : 4500); // errors stay longer
    },
    [dismiss],
  );
  const api = useMemo<Api>(
    () => ({ ok: (t) => push("ok", t), error: (t) => push("bad", t), info: (t) => push("info", t) }),
    [push],
  );
  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toasts" role="region" aria-label="Notifications" aria-live="polite">
        <AnimatePresence initial={false}>
          {items.map((t) => {
            const Icon = t.tone === "ok" ? CheckCircle : t.tone === "bad" ? XCircle : Info;
            return (
              <motion.div
                key={t.id}
                layout
                className={`toast ${t.tone}`}
                role={t.tone === "bad" ? "alert" : "status"}
                initial={{ opacity: 0, x: 24 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 24, transition: { duration: 0.15 } }}
                transition={{ duration: 0.2, ease: [0.2, 0.7, 0.2, 1] }}
              >
                <Icon size={18} aria-hidden />
                <span className="grow">{t.text}</span>
                <button className="toast-x" aria-label="Dismiss" onClick={() => dismiss(t.id)}><X size={13} aria-hidden /></button>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </Ctx.Provider>
  );
}
