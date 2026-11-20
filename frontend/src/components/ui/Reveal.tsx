"use client";

import { motion, type HTMLMotionProps } from "motion/react";

const container = { hidden: {}, show: { transition: { staggerChildren: 0.045 } } };
const item = { hidden: { opacity: 0, y: 8 }, show: { opacity: 1, y: 0, transition: { duration: 0.24, ease: [0.2, 0.7, 0.2, 1] as const } } };

/** Children that are <Item> fade and rise in sequence. Reduced-motion users get no movement (MotionConfig in the shell). */
export function Stagger(props: HTMLMotionProps<"div">) {
  return <motion.div variants={container} initial="hidden" animate="show" {...props} />;
}

export function Item(props: HTMLMotionProps<"div">) {
  return <motion.div variants={item} {...props} />;
}
