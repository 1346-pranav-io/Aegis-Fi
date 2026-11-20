"use client";

import { motion } from "motion/react";
import type { ReactNode } from "react";

// A template re-mounts on every navigation, so this runs once per page change. The shell around it stays put.
export default function Template({ children }: { children: ReactNode }) {
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.22, ease: [0.2, 0.7, 0.2, 1] }}>
      {children}
    </motion.div>
  );
}
