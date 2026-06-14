import { useEffect, useLayoutEffect, useRef } from "react";

/**
 * Modal niceties: close on Escape and lock body scroll while the modal is open.
 * Pass the close handler; call inside the modal component.
 */
export function useModal(onClose: () => void): void {
  // Keep the latest onClose in a ref so the keydown listener (registered once)
  // never goes stale even though callers pass a fresh inline arrow each render.
  const onCloseRef = useRef(onClose);
  useLayoutEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", handleKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handleKey);
      // Restore unconditionally — acceptable for this single-page viewer and
      // avoids permanent scroll-lock from nested modals / effect re-runs.
      document.body.style.overflow = "";
    };
  }, []);
}
