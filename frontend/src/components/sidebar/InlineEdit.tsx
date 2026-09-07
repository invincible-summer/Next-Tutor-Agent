"use client";
import { useEffect, useRef, useState } from "react";

/** Inline rename/create input: autofocus + select-all, Enter/blur commits,
 *  Escape cancels. Empty values cancel instead of committing. */
export function InlineEdit({
  initialValue = "",
  placeholder,
  onCommit,
  onCancel,
}: {
  initialValue?: string;
  placeholder?: string;
  onCommit: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initialValue);
  const inputRef = useRef<HTMLInputElement>(null);
  // Guard against blur firing after an Enter commit (would commit twice).
  const doneRef = useRef(false);

  useEffect(() => {
    const el = inputRef.current;
    if (el) { el.focus(); el.select(); }
  }, []);

  const commit = () => {
    if (doneRef.current) return;
    doneRef.current = true;
    const v = value.trim();
    if (v) onCommit(v);
    else onCancel();
  };

  return (
    <input
      ref={inputRef}
      value={value}
      placeholder={placeholder}
      onChange={(e) => setValue(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.preventDefault(); commit(); }
        if (e.key === "Escape") { doneRef.current = true; onCancel(); }
      }}
      onBlur={commit}
      className="w-full rounded-md border border-accent/40 bg-bg px-2 py-1 text-xs text-fg outline-none"
    />
  );
}
