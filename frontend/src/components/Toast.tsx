import { useEffect, useState } from 'react';
import { CheckCircle, AlertCircle, X } from 'lucide-react';

export interface ToastMessage {
  type: 'success' | 'error';
  message: string;
}

interface ToastProps {
  toast: ToastMessage | null;
  onDismiss: () => void;
  /** Auto-dismiss delay in ms. Defaults to 5000 for errors, 3000 for success. */
  duration?: number;
}

export function Toast({ toast, onDismiss, duration }: ToastProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!toast) {
      setVisible(false);
      return;
    }

    // Trigger enter animation on next tick
    const showTimer = setTimeout(() => setVisible(true), 10);

    const delay = duration ?? (toast.type === 'error' ? 6000 : 3000);
    const dismissTimer = setTimeout(() => {
      setVisible(false);
      // Let the CSS transition finish before clearing the toast
      setTimeout(onDismiss, 300);
    }, delay);

    return () => {
      clearTimeout(showTimer);
      clearTimeout(dismissTimer);
    };
  }, [toast]);

  if (!toast) return null;

  const isError = toast.type === 'error';

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={`
        fixed bottom-6 right-6 z-50 flex items-start gap-3
        max-w-sm w-full rounded-lg shadow-lg px-4 py-3
        transition-all duration-300 ease-in-out
        ${isError ? 'bg-red-50 border border-red-200 text-red-800' : 'bg-green-50 border border-green-200 text-green-800'}
        ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4 pointer-events-none'}
      `}
    >
      <div className="flex-shrink-0 mt-0.5">
        {isError
          ? <AlertCircle className="w-5 h-5 text-red-500" />
          : <CheckCircle className="w-5 h-5 text-green-500" />
        }
      </div>

      <p className="flex-1 text-sm font-medium leading-snug">
        {toast.message}
      </p>

      <button
        onClick={() => {
          setVisible(false);
          setTimeout(onDismiss, 300);
        }}
        className={`
          flex-shrink-0 rounded p-0.5 transition-colors
          ${isError ? 'hover:bg-red-100' : 'hover:bg-green-100'}
        `}
        aria-label="Dismiss"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}
