"use client";

import { Dialog } from "./ui/Dialog";
import { Kbd } from "./ui/Tip";

const ROWS: Array<{ section: string; items: Array<{ keys: string[]; what: string; plus?: boolean }> }> = [
  {
    section: "Anywhere",
    items: [
      { keys: ["Ctrl", "K"], plus: true, what: "Open the command palette (Cmd K on Mac)" },
      { keys: ["Ctrl", "B"], plus: true, what: "Collapse or expand the sidebar" },
      { keys: ["/"], what: "Focus the search box on this page (or open the palette)" },
      { keys: ["?"], what: "Show this list" },
      { keys: ["g", "o"], what: "Go to Overview" },
      { keys: ["g", "q"], what: "Go to the case queue" },
      { keys: ["g", "a"], what: "Go to Approvals" },
      { keys: ["g", "l"], what: "Go to the Audit log" },
      { keys: ["g", "s"], what: "Go to System" },
      { keys: ["g", "t"], what: "Go to Settings" },
      { keys: ["Esc"], what: "Close a dialog or menu, or clear a search" },
    ],
  },
  {
    section: "Tables",
    items: [
      { keys: ["j"], what: "Next row (Arrow Down works too)" },
      { keys: ["k"], what: "Previous row (Arrow Up works too)" },
      { keys: ["Enter"], what: "Open the focused row, where it opens something" },
      { keys: ["Space"], what: "Expand or collapse the focused row, where it expands" },
      { keys: ["Right-click"], what: "Row actions (the ... button does the same)" },
    ],
  },
];

export function ShortcutsHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} title="Keyboard shortcuts" onClose={onClose}>
      {ROWS.map((s) => (
        <section key={s.section}>
          <h3 style={{ marginBottom: 8 }}>{s.section}</h3>
          <dl className="keys">
            {s.items.map((i) => (
              <div key={i.what}>
                <dt>{i.keys.map((k, n) => <span key={k}>{n > 0 && <span className="muted small"> {i.plus ? "+" : "then"} </span>}<Kbd>{k}</Kbd></span>)}</dt>
                <dd>{i.what}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
      <p className="muted small" style={{ margin: 0 }}>Shortcuts are ignored while you are typing in a field.</p>
    </Dialog>
  );
}
