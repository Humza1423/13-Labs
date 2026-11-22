// Adapted from Alibi (rayanali2/HackPrinceton), web/src/dialog.tsx at f109e4d5c6720ffa433aa65b0800994afbe416d7.
import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

/** Native modal supplies focus containment, Escape and return-to-trigger behavior. */
export function Dialog({
  title,
  children,
  onClose,
  wide = false,
  returnFocus,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  returnFocus?: HTMLElement | null;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = ref.current;
    const trigger =
      returnFocus ?? (document.activeElement as HTMLElement | null);
    el?.showModal();
    // Keep keyboard focus inside the app even when Tab would otherwise visit browser chrome.
    const contain = (e: KeyboardEvent) => {
      if (e.key !== "Tab" || !el) return;
      const candidates = Array.from(
        el.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((node) => node.getClientRects().length && node.tabIndex >= 0);
      const first = candidates[0],
        last = candidates[candidates.length - 1];
      if (!first) {
        e.preventDefault();
        el.focus();
      } else if (
        e.shiftKey &&
        (document.activeElement === first || document.activeElement === el)
      ) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    el?.addEventListener("keydown", contain);
    return () => {
      el?.removeEventListener("keydown", contain);
      el?.close();
      if (trigger?.isConnected) trigger.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      tabIndex={-1}
      className={wide ? "drawer wide" : "drawer"}
      aria-label={title}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header className="drawer-head">
        <h2>{title}</h2>
        <button onClick={onClose} aria-label={`Close ${title}`}>
          Close <span aria-hidden>×</span>
        </button>
      </header>
      {children}
    </dialog>
  );
}
