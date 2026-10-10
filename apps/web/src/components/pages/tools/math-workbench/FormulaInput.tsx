"use client";

/**
 * Friendly math text field (plan D5): a symbol strip inserts common tokens
 * at the caret, `(` auto-closes (or wraps the selection), and a KaTeX
 * preview renders the parsed expression live. The field stays plain text —
 * the domain normalizes ×, ÷, π, θ, ** aliases at compile time; this layer
 * never parses LaTeX.
 *
 * Two layouts: full mode (symbol strip + inline preview, used by the math
 * tools panel) and compact mode for plot rows — symbols strip removed and
 * the preview becomes a focus-only popover so a row keeps its height until
 * the user actually edits it.
 */

import { useMemo, useRef, useState } from "react";
import katex from "katex";
import { compileExpression, type EvaluationContext } from "./workbench-types.ts";
import { astToLatex } from "./latex.ts";

const SYMBOLS: Array<{ insert: string; label: string; title: string }> = [
  { insert: "×", label: "×", title: "multiply" },
  { insert: "÷", label: "÷", title: "divide" },
  { insert: "^", label: "^", title: "power" },
  { insert: "sqrt(", label: "√", title: "square root" },
  { insert: "π", label: "π", title: "pi" },
  { insert: "θ", label: "θ", title: "theta" },
  { insert: "(", label: "(", title: "left paren" },
  { insert: ")", label: ")", title: "right paren" },
];

export function FormulaInput({
  value, onChange, onCommit, onEscape, commitOnBlur = true, placeholder, invalid, disabled, testid, ariaLabel, previewVariables, ctx, compact,
}: {
  value: string;
  onChange: (text: string) => void;
  onCommit?: () => void;
  /** Escape reverts the draft (parent clears the draft key) and blurs. */
  onEscape?: () => void;
  /** Full rows commit on blur; tool panels run on Enter only. */
  commitOnBlur?: boolean;
  placeholder?: string;
  invalid?: boolean;
  disabled?: boolean;
  testid?: string;
  ariaLabel?: string;
  /** Free variables allowed at this input (plot axes + user parameters). */
  previewVariables: readonly string[];
  ctx: EvaluationContext | null;
  compact?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [focused, setFocused] = useState(false);

  /** Replace text around the caret (or wrap the selection), keep focus. */
  const editAtCaret = (fn: (before: string, sel: string, after: string) => { text: string; caret: number }) => {
    const input = inputRef.current;
    if (!input || disabled) return;
    const start = input.selectionStart ?? value.length;
    const end = input.selectionEnd ?? value.length;
    const { text, caret } = fn(value.slice(0, start), value.slice(start, end), value.slice(end));
    onChange(text);
    requestAnimationFrame(() => {
      input.focus();
      input.setSelectionRange(caret, caret);
    });
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      onCommit?.();
      return;
    }
    if (event.key === "Escape" && onEscape) {
      event.preventDefault();
      onEscape();
      inputRef.current?.blur();
      return;
    }
    if (event.key === "(") {
      event.preventDefault();
      editAtCaret((before, sel, after) => {
        if (sel.length > 0) return { text: `${before}(${sel})${after}`, caret: before.length + sel.length + 2 };
        return { text: `${before}()${after}`, caret: before.length + 1 };
      });
    }
  };

  const variablesKey = previewVariables.join(",");
  const preview = useMemo(() => {
    const text = value.trim();
    if (!ctx || text.length === 0) return null;
    const compiled = compileExpression(text, { variables: [...previewVariables, ...Object.keys(ctx.parameters)], functions: ctx.functions });
    if (!compiled.ok) return null;
    const latex = astToLatex(compiled.value.ast);
    if (!latex) return null;
    try {
      return katex.renderToString(latex, { throwOnError: false, output: "html" });
    } catch {
      return null;
    }
    // Preview depends only on the text and the symbol table.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, ctx, variablesKey]);

  return (
    <div className={`formula-input ${invalid ? "is-invalid" : ""} ${compact ? "is-compact" : ""}`}>
      <div className="formula-input-row">
        <input
          ref={inputRef}
          className="formula-field"
          value={value}
          placeholder={placeholder}
          spellCheck={false}
          autoComplete="off"
          inputMode="text"
          disabled={disabled}
          data-testid={testid}
          aria-label={ariaLabel}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => { setFocused(false); if (commitOnBlur) onCommit?.(); }}
        />
      </div>
      {!compact && (
        <div className="formula-symbols" role="toolbar" aria-label="math symbols">
          {SYMBOLS.map((symbol) => (
            <button
              key={symbol.label}
              type="button"
              className="formula-symbol"
              disabled={disabled}
              title={symbol.title}
              aria-label={symbol.title}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                if (symbol.insert.endsWith("(")) {
                  editAtCaret((before, sel, after) => ({ text: `${before}${symbol.insert})${after}`, caret: before.length + symbol.insert.length }));
                } else {
                  editAtCaret((before, _sel, after) => ({ text: `${before}${symbol.insert}${after}`, caret: before.length + symbol.insert.length }));
                }
              }}
            >{symbol.label}</button>
          ))}
        </div>
      )}
      {preview && (compact
        ? (
          focused && (
            <div className="formula-preview-pop" aria-hidden dangerouslySetInnerHTML={{ __html: preview }} data-no-export />
          )
        )
        : (
          <div className="formula-preview" aria-hidden dangerouslySetInnerHTML={{ __html: preview }} />
        )
      )}
    </div>
  );
}
