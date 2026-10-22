import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react';
import { useApp } from '../state';

export function Toasts() {
  const { toasts, dismissToast } = useApp();
  return (
    <div className="toasts">
      {toasts.map((toast) => {
        const Icon = toast.tone === 'success' ? CheckCircle2 : toast.tone === 'error' ? AlertTriangle : Info;
        return (
          <div key={toast.id} className={`toast ${toast.tone}`}>
            <Icon size={17} />
            <span>{toast.message}</span>
            <button onClick={() => dismissToast(toast.id)} title="关闭">
              <X size={13} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
