export default function Modal({
  open,
  title,
  subtitle,
  children,
  footer,
  onClose,
  size = "md",
}) {
  if (!open) return null;

  return (
    <div className="app-modal-backdrop" onClick={onClose}>
      <div
        className={`app-modal app-modal-${size}`}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="app-modal-header">
          <div>
            <h3>{title}</h3>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>

          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="Close modal"
          >
            ×
          </button>
        </div>

        <div className="app-modal-body">{children}</div>

        {footer ? <div className="app-modal-footer">{footer}</div> : null}
      </div>
    </div>
  );
}
