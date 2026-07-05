import React, { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { CheckCircle2, XCircle, AlertTriangle, RefreshCw, ArrowLeft, Clock, FileText, Landmark, Download } from 'lucide-react';
import { api } from '../services/api';
import type { ProcessingStateResponse, AuditEntry } from '../services/api';

/* ─── Helpers ─────────────────────────────────────────────────────── */

const getConfidenceBadge = (score: number) => {
  if (score >= 0.8) {
    return <span className='confidence-badge confidence-high'>HIGH</span>;
  }
  if (score >= 0.5) {
    return <span className='confidence-badge confidence-medium'>MED</span>;
  }
  return <span className='confidence-badge confidence-low'>LOW</span>;
};

const getAuditBadgeColor = (result: string): string => {
  const lower = result.toLowerCase();
  if (lower.includes('success') || lower.includes('approved') || lower.includes('complete')) {
    return 'var(--success)';
  }
  if (lower.includes('failure') || lower.includes('error') || lower.includes('rejected')) {
    return 'var(--error)';
  }
  return 'var(--primary)';
};

/* ─── Component ───────────────────────────────────────────────────── */

export const Processing: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [state, setState] = useState<ProcessingStateResponse | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isPolling, setIsPolling] = useState(true);
  const [retryLoading, setRetryLoading] = useState(false);

  // Manual override and edit states
  const [isEditing, setIsEditing] = useState(false);
  const [editForm, setEditForm] = useState<any>(null);
  const [showOverride, setShowOverride] = useState<'approve' | 'reject' | null>(null);
  const [overrideReason, setOverrideReason] = useState('');
  const [overrideLoading, setOverrideLoading] = useState(false);

  useEffect(() => {
    if (state?.extracted_invoice && !editForm) {
      const extracted = state.extracted_invoice;
      setEditForm({
        vendor_name: extracted.vendor_name || '',
        total_amount: extracted.total_amount ?? 0.0,
        invoice_date: extracted.invoice_date ? extracted.invoice_date.split('T')[0] : '',
        due_date: extracted.due_date ? extracted.due_date.split('T')[0] : '',
        line_items: extracted.line_items ? extracted.line_items.map((item: any) => ({
          item_name: item.item_name || '',
          quantity: item.quantity ?? 0,
          unit_price: item.unit_price ?? 0.0,
          total: item.total ?? 0.0
        })) : []
      });
    }
  }, [state, editForm]);

  const handleAddLineItem = () => {
    if (!editForm) return;
    setEditForm((prev: any) => ({
      ...prev,
      line_items: [...prev.line_items, { item_name: '', quantity: 1, unit_price: 0, total: 0 }]
    }));
  };

  const handleRemoveLineItem = (index: number) => {
    if (!editForm) return;
    setEditForm((prev: any) => ({
      ...prev,
      line_items: prev.line_items.filter((_: any, idx: number) => idx !== index)
    }));
  };

  const handleLineItemChange = (index: number, field: string, value: any) => {
    if (!editForm) return;
    setEditForm((prev: any) => {
      const nextItems = [...prev.line_items];
      nextItems[index] = {
        ...nextItems[index],
        [field]: value
      };
      if (field === 'quantity' || field === 'unit_price') {
        const qty = field === 'quantity' ? value : nextItems[index].quantity;
        const price = field === 'unit_price' ? value : nextItems[index].unit_price;
        nextItems[index].total = qty * price;
      }
      return {
        ...prev,
        line_items: nextItems
      };
    });
  };

  const fetchData = useCallback(async () => {
    if (!id) return false;
    try {
      const response = await api.getInvoiceStatus(id);
      setState(response);

      // Fetch audit logs
      try {
        const auditResponse = await api.getInvoiceAudit(id);
        setAudit(auditResponse.audit_entries);
      } catch (e) {
        console.warn('Failed to fetch audit log', e);
      }

      // Determine if we should stop polling
      const stage = response.current_stage;
      const hasErrors = response.error_log && response.error_log.length > 0;
      const isFinished = stage === 'audit' || hasErrors || response.payment_receipt?.status === 'failure';

      return isFinished;
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail || 'Failed to fetch invoice status.');
      return true; // Stop polling on critical failure
    }
  }, [id]);

  const handleOverride = async () => {
    if (!id || !showOverride || !overrideReason.trim()) return;
    setOverrideLoading(true);
    setError(null);
    try {
      await api.overrideInvoice(id, {
        action: showOverride,
        reason: overrideReason
      });
      setShowOverride(null);
      setOverrideReason('');
      setIsPolling(true);
      await fetchData();
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail || 'Override failed.');
    } finally {
      setOverrideLoading(false);
    }
  };

  const handleSaveEdits = async () => {
    if (!id || !editForm) return;
    setOverrideLoading(true);
    setError(null);
    try {
      await api.overrideInvoice(id, {
        action: 'reprocess',
        reason: 'User manual correction of extracted data',
        corrected_extracted_invoice: {
          invoice_id: id,
          vendor_name: editForm.vendor_name,
          total_amount: parseFloat(editForm.total_amount),
          invoice_date: editForm.invoice_date,
          due_date: editForm.due_date,
          line_items: editForm.line_items.map((item: any) => ({
            item_name: item.item_name,
            quantity: parseInt(item.quantity, 10) || 0,
            unit_price: parseFloat(item.unit_price) || 0,
            total: (parseInt(item.quantity, 10) || 0) * (parseFloat(item.unit_price) || 0)
          }))
        }
      });
      setIsEditing(false);
      setIsPolling(true);
      await fetchData();
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail || 'Failed to save edits and re-process.');
    } finally {
      setOverrideLoading(false);
    }
  };

  useEffect(() => {
    if (!id) return;

    let pollInterval: any;

    const runPoll = async () => {
      const shouldStop = await fetchData();
      if (shouldStop) {
        setIsPolling(false);
      }
    };

    if (isPolling) {
      runPoll();
      pollInterval = setInterval(runPoll, 1500);
    } else {
      fetchData();
    }

    return () => {
      if (pollInterval) clearInterval(pollInterval);
    };
  }, [id, isPolling, fetchData]);

  const handleRetry = async () => {
    if (!id) return;
    setRetryLoading(true);
    setError(null);
    try {
      await api.retryInvoice(id);
      setIsPolling(true);
      await fetchData();
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail || 'Failed to retry processing.');
    } finally {
      setRetryLoading(false);
    }
  };

  /* ─── CSV Export ──────────────────────────────────────────────── */

  const handleExportAuditCSV = () => {
    if (audit.length === 0) return;

    const header = 'Timestamp,Agent,Action,Result,Duration(ms),Error';
    const rows = audit.map((entry) => {
      const escape = (val: string | undefined | null) => {
        if (!val) return '';
        // Escape double-quotes and wrap in quotes if the value contains commas, quotes, or newlines
        const escaped = val.replace(/"/g, '""');
        return `"${escaped}"`;
      };
      return [
        escape(entry.timestamp),
        escape(entry.agent_name),
        escape(entry.action),
        escape(entry.result),
        entry.duration_ms ?? '',
        escape(entry.error_msg),
      ].join(',');
    });

    const csvContent = [header, ...rows].join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = window.URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `audit_log_${id || 'unknown'}.csv`;
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    window.URL.revokeObjectURL(url);
  };

  /* ─── Error state (no data yet) ───────────────────────────────── */

  if (error && !state) {
    return (
      <div className="error-state-container">
        <XCircle size={48} className="text-red-500 mb-4" />
        <h2>Error Loading Invoice</h2>
        <p className="text-muted">{error}</p>
        <Link to="/" className="btn btn-outline mt-4">
          <ArrowLeft size={16} /> Return to Home
        </Link>
      </div>
    );
  }

  /* ─── Skeleton loading state ──────────────────────────────────── */

  if (!state) {
    return (
      <div className="processing-container">
        <div className="header-actions">
          <div className="skeleton skeleton-text" style={{ height: 32, width: '40%' }} />
        </div>
        <div className="processing-layout">
          {/* Left: skeleton pipeline panel */}
          <div className="card skeleton-card" style={{ height: 300 }}>
            <div className="skeleton skeleton-text" style={{ width: '60%', height: 20, marginBottom: 24 }} />
            <div className="skeleton skeleton-text-sm" style={{ width: '80%', height: 14, marginBottom: 16 }} />
            <div className="skeleton skeleton-text-sm" style={{ width: '70%', height: 14, marginBottom: 16 }} />
            <div className="skeleton skeleton-text-sm" style={{ width: '75%', height: 14, marginBottom: 16 }} />
            <div className="skeleton skeleton-text-sm" style={{ width: '65%', height: 14 }} />
          </div>
          {/* Right: two skeleton detail cards */}
          <div className="details-panel">
            <div className="card skeleton-card" style={{ height: 200 }}>
              <div className="skeleton skeleton-text" style={{ width: '50%', height: 18, marginBottom: 20 }} />
              <div className="skeleton skeleton-text-sm" style={{ width: '90%', height: 14, marginBottom: 12 }} />
              <div className="skeleton skeleton-text-sm" style={{ width: '85%', height: 14, marginBottom: 12 }} />
              <div className="skeleton skeleton-text-sm" style={{ width: '70%', height: 14 }} />
            </div>
            <div className="card skeleton-card" style={{ height: 200 }}>
              <div className="skeleton skeleton-text" style={{ width: '45%', height: 18, marginBottom: 20 }} />
              <div className="skeleton skeleton-text-sm" style={{ width: '80%', height: 14, marginBottom: 12 }} />
              <div className="skeleton skeleton-text-sm" style={{ width: '75%', height: 14, marginBottom: 12 }} />
              <div className="skeleton skeleton-text-sm" style={{ width: '60%', height: 14 }} />
            </div>
          </div>
        </div>
      </div>
    );
  }

  /* ─── Stage definitions & helpers ─────────────────────────────── */

  const stages = [
    { key: 'ingestion', label: 'Ingestion', desc: 'Parsing & Extraction' },
    { key: 'validation', label: 'Validation', desc: 'Inventory & Vendor Check' },
    { key: 'approval', label: 'Approval', desc: 'Finance Review' },
    { key: 'payment', label: 'Payment', desc: 'Mock Gateway Settlement' },
  ];

  const getStageStatus = (stageKey: string) => {
    const current = state.current_stage;
    const stageOrder = ['ingestion', 'validation', 'approval', 'payment', 'audit'];
    const currentIndex = stageOrder.indexOf(current);
    const stageIndex = stageOrder.indexOf(stageKey);

    if (state.error_log && state.error_log.length > 0 && current === stageKey) {
      return 'error';
    }

    if (stageKey === 'payment' && state.payment_receipt?.status === 'failure') {
      return 'error';
    }

    if (currentIndex > stageIndex) return 'completed';
    if (currentIndex === stageIndex) return 'active';
    return 'pending';
  };

  const getStageIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle2 className="step-icon text-green" size={24} />;
      case 'error':
        return <XCircle className="step-icon text-red" size={24} />;
      case 'active':
        return <LoaderSpinner size={24} className="step-icon text-primary" />;
      default:
        return <div className="step-dot" />;
    }
  };

  const getFinalStatusClass = () => {
    if (state.error_log && state.error_log.length > 0) return 'status-badge status-error';
    if (state.payment_receipt?.status === 'success') return 'status-badge status-approved';
    if (state.payment_receipt?.status === 'failure') return 'status-badge status-error';
    if (state.payment_receipt?.status === 'skipped') return 'status-badge status-rejected';
    if (state.approval_decision?.status === 'rejected' || state.validation_result?.status === 'reject') return 'status-badge status-rejected';
    return 'status-badge status-processing';
  };

  const getFinalStatusText = () => {
    if (state.error_log && state.error_log.length > 0) return 'System Error';
    if (state.payment_receipt?.status === 'success') return 'Approved & Paid';
    if (state.payment_receipt?.status === 'failure') return 'Payment Failed';
    if (state.payment_receipt?.status === 'skipped') return 'Rejected';
    if (state.approval_decision?.status === 'rejected') return 'Rejected';
    if (state.validation_result?.status === 'reject') return 'Rejected (Validation)';
    return 'Processing...';
  };

  /* ─── Extraction method badge ─────────────────────────────────── */

  const method = state.extracted_invoice?.extraction_method;

  /* ─── Confidence scores shorthand ─────────────────────────────── */

  const confidence = state.extracted_invoice?.confidence_scores;

  return (
    <div className="processing-container">
      <div className="header-actions">
        <Link to="/dashboard" className="back-link">
          <ArrowLeft size={16} /> Dashboard
        </Link>
        <div className="invoice-header-info">
          <h2>Invoice: {state.invoice_id}</h2>
          <span className={getFinalStatusClass()}>{getFinalStatusText()}</span>
        </div>
      </div>

      <div className="processing-layout">
        {/* Left Side: Pipeline Steps & Actions */}
        <div className="pipeline-panel card">
          <h3 className="section-title">Processing Timeline</h3>
          <div className="steps-container">
            {stages.map((stage) => {
              const status = getStageStatus(stage.key);
              return (
                <div key={stage.key} className={`step-item ${status}`}>
                  <div className="step-left">
                    {getStageIcon(status)}
                    <div className="step-line" />
                  </div>
                  <div className="step-right">
                    <h4 className="step-label">{stage.label}</h4>
                    <p className="step-desc">{stage.desc}</p>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Action buttons */}
          <div className="pipeline-actions">
            {!isPolling && (state.error_log.length > 0 || state.payment_receipt?.status === 'failure') && (
              <button className="btn btn-primary" onClick={handleRetry} disabled={retryLoading}>
                <RefreshCw className={retryLoading ? 'spinner' : ''} size={16} />
                Retry Processing
              </button>
            )}

            {/* Manual Overrides */}
            {!isPolling &&
              state.current_stage !== 'pending' &&
              state.payment_receipt?.status !== 'success' &&
              state.payment_receipt?.status !== 'skipped' &&
              state.approval_decision?.status !== 'rejected' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '16px', borderTop: '1px solid var(--border-color)', paddingTop: '16px' }}>
                <span className="expanded-section-title">Manual Actions</span>
                <div className="manual-actions-buttons" style={{ display: 'flex', gap: '8px' }}>
                  <button
                    className="btn btn-outline btn-sm text-green"
                    style={{ flex: 1, borderColor: 'var(--success)' }}
                    onClick={() => setShowOverride('approve')}
                    disabled={overrideLoading}
                  >
                    Force Approve
                  </button>
                  <button
                    className="btn btn-outline btn-sm text-red"
                    style={{ flex: 1, borderColor: 'var(--error)' }}
                    onClick={() => setShowOverride('reject')}
                    disabled={overrideLoading}
                  >
                    Force Reject
                  </button>
                </div>
              </div>
            )}

            {showOverride && (
              <div className="card" style={{ marginTop: '12px', padding: '16px', border: '1px solid var(--border-color)' }}>
                <h4 style={{ fontSize: '0.95rem', marginBottom: '8px', fontWeight: 600 }}>
                  Reason for Force {showOverride === 'approve' ? 'Approval' : 'Rejection'}
                </h4>
                <textarea
                  value={overrideReason}
                  onChange={(e) => setOverrideReason(e.target.value)}
                  placeholder="Provide audit reasoning..."
                  style={{
                    width: '100%',
                    height: '80px',
                    padding: '8px',
                    borderRadius: '4px',
                    border: '1px solid var(--border-color)',
                    backgroundColor: 'var(--bg-primary)',
                    color: 'var(--text-primary)',
                    fontSize: '0.85rem',
                    marginBottom: '8px',
                    resize: 'none'
                  }}
                />
                <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                  <button className="btn btn-outline btn-sm" onClick={() => setShowOverride(null)}>
                    Cancel
                  </button>
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={handleOverride}
                    disabled={overrideLoading || !overrideReason.trim()}
                  >
                    {overrideLoading ? <LoaderSpinner size={12} /> : 'Submit'}
                  </button>
                </div>
              </div>
            )}

            {!isPolling && (
              <Link to="/dashboard" className="btn btn-outline" style={{ marginTop: '8px' }}>
                Back to Dashboard
              </Link>
            )}
          </div>
        </div>

        {/* Right Side: Step details dynamically displaying */}
        <div className="details-panel">
          {/* Section 1: Extracted Info */}
          {state.extracted_invoice && (
            <div className="card details-card animate-fade-in">
              <div className="card-header" style={{ display: 'flex', alignItems: 'center' }}>
                <FileText className="text-primary" size={20} />
                <h3 style={{ margin: 0 }}>Extracted Data</h3>
                {method && (
                  <span className={`confidence-badge ${method === 'grok' ? 'confidence-high' : 'confidence-medium'}`} style={{ marginLeft: '12px' }}>
                    {method === 'grok' ? '🤖 Grok LLM' : '📄 Parser Fallback'}
                  </span>
                )}
                {!isEditing && !isPolling && (
                  <button
                    className="btn btn-outline btn-sm"
                    style={{ marginLeft: 'auto', padding: '4px 10px', fontSize: '0.8rem' }}
                    onClick={() => setIsEditing(true)}
                  >
                    Edit Data
                  </button>
                )}
              </div>

              {isEditing && editForm ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', marginTop: '12px' }}>
                  <div className="grid grid-2">
                    <div className="data-item">
                      <label>Vendor</label>
                      <input
                        type="text"
                        value={editForm.vendor_name}
                        onChange={(e) => setEditForm({ ...editForm, vendor_name: e.target.value })}
                        style={{
                          padding: '10px',
                          borderRadius: '8px',
                          border: '1px solid var(--border-color)',
                          backgroundColor: 'var(--bg-primary)',
                          color: 'var(--text-primary)',
                          fontSize: '0.95rem',
                          outline: 'none'
                        }}
                      />
                    </div>
                    <div className="data-item">
                      <label>Total Amount ($)</label>
                      <input
                        type="number"
                        step="0.01"
                        value={editForm.total_amount}
                        onChange={(e) => setEditForm({ ...editForm, total_amount: parseFloat(e.target.value) || 0 })}
                        style={{
                          padding: '10px',
                          borderRadius: '8px',
                          border: '1px solid var(--border-color)',
                          backgroundColor: 'var(--bg-primary)',
                          color: 'var(--text-primary)',
                          fontSize: '0.95rem',
                          outline: 'none'
                        }}
                      />
                    </div>
                    <div className="data-item">
                      <label>Invoice Date</label>
                      <input
                        type="date"
                        value={editForm.invoice_date}
                        onChange={(e) => setEditForm({ ...editForm, invoice_date: e.target.value })}
                        style={{
                          padding: '10px',
                          borderRadius: '8px',
                          border: '1px solid var(--border-color)',
                          backgroundColor: 'var(--bg-primary)',
                          color: 'var(--text-primary)',
                          fontSize: '0.95rem',
                          outline: 'none'
                        }}
                      />
                    </div>
                    <div className="data-item">
                      <label>Due Date</label>
                      <input
                        type="date"
                        value={editForm.due_date}
                        onChange={(e) => setEditForm({ ...editForm, due_date: e.target.value })}
                        style={{
                          padding: '10px',
                          borderRadius: '8px',
                          border: '1px solid var(--border-color)',
                          backgroundColor: 'var(--bg-primary)',
                          color: 'var(--text-primary)',
                          fontSize: '0.95rem',
                          outline: 'none'
                        }}
                      />
                    </div>
                  </div>

                  <div className="line-items-section">
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                      <h4 style={{ margin: 0 }}>Line Items</h4>
                      <button className="btn btn-outline btn-sm" onClick={handleAddLineItem}>
                        + Add Item
                      </button>
                    </div>
                    <table className="mini-table">
                      <thead>
                        <tr>
                          <th>Item</th>
                          <th className="text-center" style={{ width: '80px' }}>Qty</th>
                          <th className="text-right" style={{ width: '120px' }}>Price</th>
                          <th className="text-right" style={{ width: '120px' }}>Total</th>
                          <th className="text-center" style={{ width: '80px' }}>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {editForm.line_items.map((item: any, index: number) => (
                          <tr key={index}>
                            <td>
                              <input
                                type="text"
                                value={item.item_name}
                                onChange={(e) => handleLineItemChange(index, 'item_name', e.target.value)}
                                style={{
                                  width: '100%',
                                  padding: '6px',
                                  borderRadius: '4px',
                                  border: '1px solid var(--border-color)',
                                  backgroundColor: 'var(--bg-primary)',
                                  color: 'var(--text-primary)',
                                  fontSize: '0.85rem'
                                }}
                              />
                            </td>
                            <td>
                              <input
                                type="number"
                                value={item.quantity}
                                onChange={(e) => handleLineItemChange(index, 'quantity', parseInt(e.target.value, 10) || 0)}
                                style={{
                                  width: '100%',
                                  padding: '6px',
                                  borderRadius: '4px',
                                  border: '1px solid var(--border-color)',
                                  backgroundColor: 'var(--bg-primary)',
                                  color: 'var(--text-primary)',
                                  fontSize: '0.85rem',
                                  textAlign: 'center'
                                }}
                              />
                            </td>
                            <td>
                              <input
                                type="number"
                                step="0.01"
                                value={item.unit_price}
                                onChange={(e) => handleLineItemChange(index, 'unit_price', parseFloat(e.target.value) || 0)}
                                style={{
                                  width: '100%',
                                  padding: '6px',
                                  borderRadius: '4px',
                                  border: '1px solid var(--border-color)',
                                  backgroundColor: 'var(--bg-primary)',
                                  color: 'var(--text-primary)',
                                  fontSize: '0.85rem',
                                  textAlign: 'right'
                                }}
                              />
                            </td>
                            <td className="text-right font-mono" style={{ fontSize: '0.9rem' }}>
                              ${((item.quantity || 0) * (item.unit_price || 0)).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                            </td>
                            <td className="text-center">
                              <button
                                className="action-button-mini text-red"
                                style={{ color: 'var(--error)' }}
                                onClick={() => handleRemoveLineItem(index)}
                              >
                                Delete
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className="edit-actions-buttons" style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '12px' }}>
                    <button className="btn btn-outline" onClick={() => setIsEditing(false)} disabled={overrideLoading}>
                      Cancel
                    </button>
                    <button className="btn btn-primary" onClick={handleSaveEdits} disabled={overrideLoading}>
                      {overrideLoading ? <LoaderSpinner size={16} /> : 'Save & Re-process'}
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="grid grid-2">
                    <div className="data-item">
                      <label>Vendor</label>
                      <span>
                        {state.extracted_invoice.vendor_name}
                        {confidence && getConfidenceBadge(confidence.vendor_name)}
                      </span>
                    </div>
                    <div className="data-item">
                      <label>Total Amount</label>
                      <span className="amount-text">
                        ${state.extracted_invoice.total_amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        {confidence && getConfidenceBadge(confidence.total_amount)}
                      </span>
                    </div>
                    <div className="data-item">
                      <label>Invoice Date</label>
                      <span>
                        {new Date(state.extracted_invoice.invoice_date).toLocaleDateString()}
                        {confidence && getConfidenceBadge(confidence.invoice_date)}
                      </span>
                    </div>
                    <div className="data-item">
                      <label>Due Date</label>
                      <span>
                        {new Date(state.extracted_invoice.due_date).toLocaleDateString()}
                        {confidence && getConfidenceBadge(confidence.due_date)}
                      </span>
                    </div>
                  </div>

                  <div className="line-items-section">
                    <h4>Line Items</h4>
                    <table className="mini-table">
                      <thead>
                        <tr>
                          <th>Item</th>
                          <th className="text-center">Qty</th>
                          <th className="text-right">Price</th>
                          <th className="text-right">Total</th>
                        </tr>
                      </thead>
                      <tbody>
                        {state.extracted_invoice.line_items.map((item, index) => (
                          <tr key={index}>
                            <td>{item.item_name}</td>
                            <td className="text-center">{item.quantity}</td>
                            <td className="text-right">${item.unit_price?.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                            <td className="text-right">${((item.unit_price || 0) * item.quantity).toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}

          {/* Section 2: Validation Results */}
          {state.validation_result && (
            <div className="card details-card animate-fade-in">
              <div className="card-header">
                <CheckCircle2 className="text-green" size={20} />
                <h3>Validation Results</h3>
              </div>
              <div className="grid grid-2">
                <div className="data-item">
                  <label>Validation Outcome</label>
                  <span className={`status-text-${state.validation_result.status}`}>
                    {state.validation_result.status.toUpperCase()}
                  </span>
                </div>
                <div className="data-item">
                  <label>Risk Score</label>
                  <span>{(state.validation_result.risk_score * 100).toFixed(0)}%</span>
                </div>
              </div>
              <div className="data-item mt-3">
                <label>Reasoning</label>
                <p className="narrative-text">{state.validation_result.reasoning}</p>
              </div>

              {state.validation_result.mismatches && state.validation_result.mismatches.length > 0 && (
                <div className="mismatches-section mt-3">
                  <h4 className="text-red">Discrepancies Flagged</h4>
                  <div className="mismatches-list">
                    {state.validation_result.mismatches.map((m, idx) => (
                      <div key={idx} className="mismatch-badge">
                        <AlertTriangle size={14} />
                        <span>
                          {m.item_name}: requested {m.requested_qty}, available stock {m.available_stock} ({m.status})
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Section 3: Approval Decision */}
          {state.approval_decision && (
            <div className="card details-card animate-fade-in">
              <div className="card-header">
                <Landmark className="text-primary" size={20} />
                <h3>Approval Assessment</h3>
              </div>
              <div className="grid grid-2">
                <div className="data-item">
                  <label>Rule Applied</label>
                  <span className="font-mono text-sm">{state.approval_decision.rule_applied}</span>
                </div>
                <div className="data-item">
                  <label>Decision</label>
                  <span className={`status-text-${state.approval_decision.status}`}>
                    {state.approval_decision.status.toUpperCase()}
                  </span>
                </div>
              </div>
              <div className="data-item mt-3">
                <label>Detailed Reasoning</label>
                <p className="narrative-text">{state.approval_decision.reasoning}</p>
              </div>
            </div>
          )}

          {/* Section 4: Payment Receipt */}
          {state.payment_receipt && (
            <div className="card details-card animate-fade-in">
              <div className="card-header">
                <Clock className="text-primary" size={20} />
                <h3>Payment Settlement</h3>
              </div>
              <div className="grid grid-2">
                <div className="data-item">
                  <label>Gateway Status</label>
                  <span className={`status-text-${state.payment_receipt.status === 'success' ? 'pass' : 'reject'}`}>
                    {state.payment_receipt.status.toUpperCase()}
                  </span>
                </div>
                {state.payment_receipt.transaction_id && (
                  <div className="data-item">
                    <label>Transaction ID</label>
                    <span className="font-mono">{state.payment_receipt.transaction_id}</span>
                  </div>
                )}
                {state.payment_receipt.amount != null && (
                  <div className="data-item">
                    <label>Payment Amount</label>
                    <span className="amount-text">
                      ${state.payment_receipt.amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </span>
                  </div>
                )}
                {state.payment_receipt.vendor_name && (
                  <div className="data-item">
                    <label>Vendor</label>
                    <span>{state.payment_receipt.vendor_name}</span>
                  </div>
                )}
              </div>
              {state.payment_receipt.error && (
                <div className="error-message mt-3">
                  <AlertTriangle size={18} />
                  <span>{state.payment_receipt.error}</span>
                </div>
              )}
            </div>
          )}

          {/* Section 5: Processing System Errors */}
          {state.error_log && state.error_log.length > 0 && (
            <div className="card details-card border-red animate-fade-in">
              <div className="card-header text-red">
                <AlertTriangle size={20} />
                <h3>System Exception Logs</h3>
              </div>
              <div className="error-logs-container">
                {state.error_log.map((err, idx) => (
                  <pre key={idx} className="error-log-block">
                    {err}
                  </pre>
                ))}
              </div>
            </div>
          )}

          {/* Section 6: Audit Lineage Timeline */}
          {audit.length > 0 && (
            <div className="card details-card animate-fade-in">
              <div className="card-header" style={{ justifyContent: 'space-between' }}>
                <h3 className="section-title mb-0">Audit Execution Log</h3>
                <button
                  className="btn btn-outline btn-sm"
                  onClick={handleExportAuditCSV}
                  title="Export audit log as CSV"
                >
                  <Download size={14} />
                  Export CSV
                </button>
              </div>
              <div className="audit-timeline">
                {audit.map((entry) => {
                  const badgeColor = getAuditBadgeColor(entry.result);
                  return (
                    <div key={entry.id} className="audit-timeline-item">
                      <div
                        className="audit-timeline-badge"
                        style={{ backgroundColor: badgeColor }}
                      />
                      <div className="audit-timeline-content">
                        <div className="audit-timeline-header">
                          <span className="agent-tag">{entry.agent_name.toUpperCase()}</span>
                          <span className="audit-duration">{entry.duration_ms}ms</span>
                        </div>
                        <p className="audit-action">{entry.action} &rarr; result: <strong>{entry.result}</strong></p>
                        {entry.error_msg && (
                          <p className="text-red text-sm mt-1">{entry.error_msg}</p>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

const LoaderSpinner: React.FC<{ size?: number; className?: string }> = ({ size = 24, className = '' }) => (
  <svg className={`spinner ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
  </svg>
);
