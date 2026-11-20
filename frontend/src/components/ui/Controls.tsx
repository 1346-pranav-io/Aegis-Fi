"use client";

import { forwardRef, type ReactNode } from "react";
import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { Kbd } from "./Tip";

/** Search input. `data-search` lets the global "/" shortcut find and focus it. */
export const SearchField = forwardRef<HTMLInputElement, {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  label: string;
}>(function SearchField({ value, onChange, placeholder, label }, ref) {
  return (
    <div className="search">
      <Search size={15} aria-hidden className="muted" />
      <input
        ref={ref}
        data-search
        type="search"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && value) { e.stopPropagation(); onChange(""); }
        }}
      />
      {value ? (
        <button className="search-x" aria-label="Clear search" onClick={() => onChange("")}><X size={13} aria-hidden /></button>
      ) : (
        <Kbd>/</Kbd>
      )}
    </div>
  );
});

export function Segmented<T extends string>({
  label, value, options, onChange,
}: {
  label: string;
  value: T;
  options: Array<{ value: T; label: string; count?: number }>;
  onChange: (v: T) => void;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
          {o.count !== undefined && <span className="num seg-n">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function SortTh({
  label, active, dir, onSort, className,
}: {
  label: string;
  active: boolean;
  dir: "asc" | "desc";
  onSort: () => void;
  className?: string;
}) {
  return (
    <th className={className} aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}>
      <button className="sort" onClick={onSort}>
        {label}
        {active ? (dir === "asc" ? <ChevronUp size={11} aria-hidden /> : <ChevronDown size={11} aria-hidden />) : <span className="sort-ph" aria-hidden />}
      </button>
    </th>
  );
}

export function SelectField({
  label, value, onChange, children,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  children: ReactNode;
}) {
  return (
    <label className="selectf">
      <span className="muted small">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>{children}</select>
    </label>
  );
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="toolbar" role="toolbar">{children}</div>;
}
