import { AlertTriangle, ShieldCheck } from 'lucide-react';
import { useApp } from '../state';

export function ConfirmDialog() {
  const { confirmState } = useApp();
  if (!confirmState) return null;
  const { options, resolve } = confirmState;
  const Icon = options.danger ? AlertTriangle : ShieldCheck;
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className={`modal ${options.danger ? 'danger' : ''}`}>
        <div className="modal-icon">
          <Icon size={22} />
        </div>
        <h2>{options.title}</h2>
        <p className="modal-desc">{options.description}</p>
        {options.details && options.details.length > 0 && (
          <ul className="modal-details">
            {options.details.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        )}
        <div className="modal-actions">
          <button className="btn ghost" onClick={() => resolve(false)}>
            取消
          </button>
          <button
            className={`btn ${options.danger ? 'danger' : 'primary'}`}
            onClick={() => resolve(true)}
            autoFocus
          >
            {options.confirmLabel ?? '继续'}
          </button>
        </div>
      </div>
    </div>
  );
}
