import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type ConfirmOptions = {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
};

export type PromptOptions = {
  title?: string;
  message: string;
  defaultValue?: string;
  confirmLabel?: string;
  cancelLabel?: string;
};

type DialogApi = {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
  prompt: (opts: PromptOptions) => Promise<string | null>;
};

const DialogContext = createContext<DialogApi | null>(null);

export function useDialog(): DialogApi {
  const ctx = useContext(DialogContext);
  if (!ctx) throw new Error("useDialog must be used within DialogProvider");
  return ctx;
}

type ConfirmState = ConfirmOptions & { resolve: (v: boolean) => void };
type PromptState = PromptOptions & { resolve: (v: string | null) => void };

export function DialogProvider({ children }: { children: ReactNode }) {
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [promptState, setPromptState] = useState<PromptState | null>(null);
  const [promptValue, setPromptValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const confirm = useCallback((opts: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      setConfirmState({ ...opts, resolve });
    });
  }, []);

  const prompt = useCallback((opts: PromptOptions) => {
    return new Promise<string | null>((resolve) => {
      setPromptValue(opts.defaultValue ?? "");
      setPromptState({ ...opts, resolve });
    });
  }, []);

  const api = useMemo(() => ({ confirm, prompt }), [confirm, prompt]);

  useEffect(() => {
    if (promptState) {
      const t = window.setTimeout(() => inputRef.current?.focus(), 0);
      return () => window.clearTimeout(t);
    }
  }, [promptState]);

  useEffect(() => {
    if (!confirmState && !promptState) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (confirmState) {
          confirmState.resolve(false);
          setConfirmState(null);
        } else if (promptState) {
          promptState.resolve(null);
          setPromptState(null);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirmState, promptState]);

  function closeConfirm(ok: boolean) {
    confirmState?.resolve(ok);
    setConfirmState(null);
  }

  function closePrompt(ok: boolean) {
    if (!promptState) return;
    promptState.resolve(ok ? promptValue : null);
    setPromptState(null);
  }

  const open = confirmState || promptState;

  return (
    <DialogContext.Provider value={api}>
      {children}
      {open && (
        <div
          className="dlg-backdrop"
          role="presentation"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) {
              if (confirmState) closeConfirm(false);
              else closePrompt(false);
            }
          }}
        >
          {confirmState && (
            <div
              className={
                "dlg-panel" +
                (confirmState.danger ? " dlg-panel-warn" : " dlg-panel-ok")
              }
              role="alertdialog"
              aria-modal="true"
              aria-labelledby="dlg-title"
              aria-describedby="dlg-body"
            >
              <div className="dlg-lead">
                <span
                  className={
                    "dlg-icon" +
                    (confirmState.danger ? " dlg-icon-warn" : " dlg-icon-ok")
                  }
                  aria-hidden="true"
                >
                  {confirmState.danger ? "!" : "?"}
                </span>
                <div className="dlg-copy">
                  <div className="dlg-eyebrow">
                    <span className="dlg-eyebrow-text">
                      {confirmState.danger ? "Warning" : "Confirm"}
                    </span>
                  </div>
                  <h2 id="dlg-title" className="dlg-title">
                    {confirmState.title || "Please confirm"}
                  </h2>
                </div>
              </div>
              <p id="dlg-body" className="dlg-body">
                {confirmState.message}
              </p>
              <div className="dlg-actions">
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => closeConfirm(false)}
                >
                  {confirmState.cancelLabel || "Cancel"}
                </button>
                <button
                  type="button"
                  className={confirmState.danger ? "danger" : "primary"}
                  onClick={() => closeConfirm(true)}
                  autoFocus
                >
                  {confirmState.confirmLabel || "Confirm"}
                </button>
              </div>
            </div>
          )}

          {promptState && (
            <div
              className="dlg-panel dlg-panel-ok"
              role="dialog"
              aria-modal="true"
              aria-labelledby="dlg-title"
              aria-describedby="dlg-body"
            >
              <div className="dlg-lead">
                <span className="dlg-icon dlg-icon-ok" aria-hidden="true">
                  +
                </span>
                <div className="dlg-copy">
                  <div className="dlg-eyebrow">
                    <span className="dlg-eyebrow-text">Input</span>
                  </div>
                  <h2 id="dlg-title" className="dlg-title">
                    {promptState.title || "Enter a value"}
                  </h2>
                </div>
              </div>
              <p id="dlg-body" className="dlg-body">
                {promptState.message}
              </p>
              <input
                ref={inputRef}
                className="dlg-input"
                value={promptValue}
                onChange={(e) => setPromptValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") closePrompt(true);
                }}
              />
              <div className="dlg-actions">
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => closePrompt(false)}
                >
                  {promptState.cancelLabel || "Cancel"}
                </button>
                <button
                  type="button"
                  className="primary"
                  onClick={() => closePrompt(true)}
                >
                  {promptState.confirmLabel || "OK"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </DialogContext.Provider>
  );
}
