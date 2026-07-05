import React, { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { Search, Filter, ArrowUpDown, RefreshCw, Plus, Download, ChevronDown, ChevronRight } from 'lucide-react';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, PieChart, Pie, Cell } from 'recharts';
import { api } from '../services/api';
import type { InvoiceSummary, StatsResponse, ProcessingStateResponse } from '../services/api';

const rechartsTooltipStyle = {
  backgroundColor: 'rgba(30, 41, 59, 0.95)',
  border: '1px solid rgba(148, 163, 184, 0.1)',
  borderRadius: '12px',
  color: '#f8fafc',
  fontSize: '0.85rem',
  boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
};

const getConfidenceClass = (score: number): string => {
  if (score >= 0.8) return 'confidence-badge confidence-high';
  if (score >= 0.5) return 'confidence-badge confidence-medium';
  return 'confidence-badge confidence-low';
};

export const Dashboard: React.FC = () => {
  const [invoices, setInvoices] = useState<InvoiceSummary[]>([]);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [sortField, setSortField] = useState<keyof InvoiceSummary>('created_at');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [loading, setLoading] = useState(true);
  const [refreshLoading, setRefreshLoading] = useState(false);

  // Expandable row state
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [expandedDetails, setExpandedDetails] = useState<Map<string, ProcessingStateResponse>>(new Map());

  const fetchDashboardData = async () => {
    try {
      const invoicesData = await api.listInvoices();
      setInvoices(invoicesData.invoices);

      const statsData = await api.getStats();
      setStats(statsData);

      // Clear expanded cache to prevent rendering stale detail data
      setExpandedDetails(new Map());
    } catch (e) {
      console.error('Failed to fetch dashboard data', e);
    }
  };

  useEffect(() => {
    const initData = async () => {
      setLoading(true);
      await fetchDashboardData();
      setLoading(false);
    };
    initData();
  }, []);

  const handleRefresh = async () => {
    setRefreshLoading(true);
    await fetchDashboardData();
    setRefreshLoading(false);
  };

  const getStatusText = (status: string) => {
    if (!status) return '—';
    switch (status) {
      case 'approved_manual':
        return 'MANUAL APPROVAL';
      case 'rejected_manual':
        return 'MANUAL REJECT';
      default:
        return status.toUpperCase();
    }
  };

  const getStatusClass = (status: string) => {
    if (!status) return 'status-badge';
    if (status === 'approved_manual') return 'status-badge status-manual-approved';
    if (status === 'rejected_manual') return 'status-badge status-manual-rejected';
    return `status-badge status-${status}`;
  };

  const handleSort = (field: keyof InvoiceSummary) => {
    if (sortField === field) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortOrder('desc');
    }
  };

  const handleRetry = async (e: React.MouseEvent, invoiceId: string) => {
    e.stopPropagation();
    e.preventDefault();
    try {
      await api.retryInvoice(invoiceId);
      handleRefresh();
    } catch (err) {
      console.error('Retry failed', err);
    }
  };

  // Toggle expanded row and fetch details if not cached
  const handleToggleExpand = useCallback(async (invoiceId: string) => {
    if (expandedRow === invoiceId) {
      setExpandedRow(null);
      return;
    }
    setExpandedRow(invoiceId);
    if (!expandedDetails.has(invoiceId)) {
      try {
        const details = await api.getInvoiceStatus(invoiceId);
        setExpandedDetails((prev) => {
          const next = new Map(prev);
          next.set(invoiceId, details);
          return next;
        });
      } catch (err) {
        console.error('Failed to fetch invoice details', err);
      }
    }
  }, [expandedRow, expandedDetails]);

  // CSV Export
  const handleDownloadCsv = useCallback(() => {
    const headers = ['Invoice ID', 'Vendor', 'Amount', 'Date', 'Stage', 'Status'];
    const rows = filteredInvoices.map((inv) => [
      inv.invoice_id,
      (inv.vendor_name || '').replace(/,/g, ' '),
      inv.total_amount !== null && inv.total_amount !== undefined
        ? inv.total_amount.toFixed(2)
        : '',
      new Date(inv.created_at).toLocaleDateString(),
      inv.current_stage,
      inv.status,
    ]);
    const csvContent = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = window.URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `invoices_export_${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    window.URL.revokeObjectURL(url);
  }, []);

  // Filter & Sort Invoices
  const filteredInvoices = invoices
    .filter((inv) => {
      const matchSearch =
        (inv.invoice_id || '').toLowerCase().includes(search.toLowerCase()) ||
        (inv.vendor_name || '').toLowerCase().includes(search.toLowerCase());
      const matchStatus =
        statusFilter === '' ||
        inv.status === statusFilter ||
        (statusFilter === 'approved' && inv.status === 'approved_manual') ||
        (statusFilter === 'rejected' && inv.status === 'rejected_manual');
      return matchSearch && matchStatus;
    })
    .sort((a, b) => {
      let valA = a[sortField] ?? '';
      let valB = b[sortField] ?? '';

      if (typeof valA === 'string') {
        valA = valA.toLowerCase();
      }
      if (typeof valB === 'string') {
        valB = valB.toLowerCase();
      }

      if (valA < valB) return sortOrder === 'asc' ? -1 : 1;
      if (valA > valB) return sortOrder === 'asc' ? 1 : -1;
      return 0;
    });

  // Skeleton loading state
  if (loading) {
    return (
      <div className="dashboard-container">
        {/* Skeleton Header */}
        <div className="dashboard-header">
          <div>
            <div className="skeleton skeleton-text" style={{ width: '320px', height: '28px', marginBottom: '8px' }} />
            <div className="skeleton skeleton-text" style={{ width: '480px', height: '16px' }} />
          </div>
        </div>

        {/* Skeleton KPI Cards */}
        <div className="kpi-grid">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="kpi-card card skeleton-card">
              <div className="skeleton skeleton-text" style={{ width: '60%', height: '14px', marginBottom: '12px' }} />
              <div className="skeleton skeleton-text" style={{ width: '40%', height: '32px', marginBottom: '8px' }} />
              <div className="skeleton skeleton-text" style={{ width: '50%', height: '12px' }} />
            </div>
          ))}
        </div>

        {/* Skeleton Table */}
        <div className="card table-card" style={{ marginTop: '24px' }}>
          <div style={{ padding: '20px' }}>
            <div className="skeleton skeleton-text" style={{ width: '100%', height: '40px', marginBottom: '16px' }} />
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div key={i} className="skeleton skeleton-text" style={{ width: '100%', height: '48px', marginBottom: '8px' }} />
            ))}
          </div>
        </div>
      </div>
    );
  }

  // Chart data formatting
  const pieData = stats
    ? [
        { name: 'Approved', value: stats.approved, color: '#10B981' },
        { name: 'Rejected', value: stats.rejected, color: '#EF4444' },
        { name: 'Processing', value: stats.processing, color: '#3B82F6' },
      ].filter((d) => d.value > 0)
    : [];

  const vendorChartData = stats
    ? Object.entries(stats.by_vendor)
        .map(([name, v]) => ({
          name: name.length > 15 ? name.substring(0, 15) + '...' : name,
          amount: v.total,
          count: v.count,
        }))
        .sort((a, b) => b.amount - a.amount)
        .slice(0, 5)
    : [];

  return (
    <div className="dashboard-container">
      {/* Header */}
      <div className="dashboard-header">
        <div>
          <h1 className="page-title">Invoice Intelligence Dashboard</h1>
          <p className="page-subtitle">Real-time status monitoring, metrics aggregation, and payment audit logs.</p>
        </div>
        <div className="header-actions">
          <button className="btn btn-outline" onClick={handleRefresh} disabled={refreshLoading}>
            <RefreshCw className={refreshLoading ? 'spinner' : ''} size={16} />
            Sync Data
          </button>
          <Link to="/" className="btn btn-primary">
            <Plus size={16} /> Ingest Invoice
          </Link>
        </div>
      </div>

      {/* KPI Cards */}
      {stats && (
        <div className="kpi-grid">
          <div className="kpi-card card">
            <div className="kpi-label">Total Ingested</div>
            <div className="kpi-value">{stats.total_invoices}</div>
            <div className="kpi-meta text-primary">{stats.processing} processing</div>
          </div>
          <div className="kpi-card card">
            <div className="kpi-label">Approval Rate</div>
            <div className="kpi-value">{(stats.approved_percent).toFixed(1)}%</div>
            <div className="kpi-meta text-green">{stats.approved} approved invoices</div>
          </div>
          <div className="kpi-card card">
            <div className="kpi-label">Total Approved Volume</div>
            <div className="kpi-value">${stats.total_amount_approved.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
            <div className="kpi-meta text-muted">Paid mock transactions</div>
          </div>
          <div className="kpi-card card">
            <div className="kpi-label">Avg. Lead Time</div>
            <div className="kpi-value">{(stats.avg_processing_time_ms / 1000).toFixed(1)}s</div>
            <div className="kpi-meta text-green">Auto-resolve rules enabled</div>
          </div>
        </div>
      )}

      {/* Charts Panel */}
      <div className="charts-grid mb-6">
        {/* Status Pie */}
        <div className="card chart-card">
          <h3 className="chart-title">Status Breakdown</h3>
          <div className="chart-container flex-center">
            {pieData.length > 0 ? (
              <ResponsiveContainer width="100%" height={240}>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={80}
                    paddingAngle={5}
                    dataKey="value"
                  >
                    {pieData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    formatter={(v) => [`${v} invoices`]}
                    contentStyle={rechartsTooltipStyle}
                  />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-muted">No status data available.</p>
            )}
            <div className="pie-legends flex-col">
              {pieData.map((item, idx) => (
                <div key={idx} className="legend-item">
                  <span className="legend-dot" style={{ backgroundColor: item.color }} />
                  <span>{item.name}: <strong>{item.value}</strong></span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Vendor Bar Chart */}
        <div className="card chart-card">
          <h3 className="chart-title">Top Vendors by Volume</h3>
          <div className="chart-container">
            {vendorChartData.length > 0 ? (
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={vendorChartData}>
                  <XAxis dataKey="name" stroke="#888888" fontSize={11} tickLine={false} />
                  <YAxis stroke="#888888" fontSize={11} tickLine={false} tickFormatter={(v) => `$${v}`} />
                  <Tooltip
                    formatter={(v) => [`$${(v as number).toLocaleString()}`]}
                    contentStyle={rechartsTooltipStyle}
                  />
                  <Bar dataKey="amount" fill="#3B82F6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-muted flex-center height-full">No vendor data available.</p>
            )}
          </div>
        </div>
      </div>

      {/* Invoice Data Table */}
      <div className="card table-card">
        {/* Table Filters */}
        <div className="table-filters">
          <div className="search-wrapper">
            <Search className="search-icon" size={18} />
            <input
              type="text"
              placeholder="Search by vendor or invoice ID..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="filter-wrapper">
            <Filter className="filter-icon" size={18} />
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All Statuses</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
              <option value="error">Error</option>
              <option value="processing">Processing</option>
            </select>
          </div>
          <button className="btn btn-outline" onClick={handleDownloadCsv} title="Download CSV">
            <Download size={16} />
            Download CSV
          </button>
        </div>

        {/* Responsive Table */}
        <div className="table-responsive">
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: '40px' }}></th>
                <th onClick={() => handleSort('invoice_id')} className="cursor-pointer">
                  Invoice ID <ArrowUpDown size={14} className="sort-icon inline" />
                </th>
                <th onClick={() => handleSort('vendor_name')} className="cursor-pointer">
                  Vendor <ArrowUpDown size={14} className="sort-icon inline" />
                </th>
                <th onClick={() => handleSort('total_amount')} className="cursor-pointer text-right">
                  Total <ArrowUpDown size={14} className="sort-icon inline" />
                </th>
                <th onClick={() => handleSort('created_at')} className="cursor-pointer">
                  Date Ingested <ArrowUpDown size={14} className="sort-icon inline" />
                </th>
                <th>Current Stage</th>
                <th>Status</th>
                <th className="text-center">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredInvoices.length > 0 ? (
                filteredInvoices.map((inv) => (
                  <React.Fragment key={inv.invoice_id}>
                    <tr
                      className="table-row-clickable"
                      onClick={() => handleToggleExpand(inv.invoice_id)}
                      style={{ cursor: 'pointer' }}
                    >
                      <td style={{ width: '40px', textAlign: 'center' }}>
                        {expandedRow === inv.invoice_id ? (
                          <ChevronDown size={16} className="text-primary" />
                        ) : (
                          <ChevronRight size={16} className="text-muted" />
                        )}
                      </td>
                      <td className="font-mono">
                        <Link
                          to={`/processing/${inv.invoice_id}`}
                          onClick={(e) => e.stopPropagation()}
                        >
                          {inv.invoice_id}
                        </Link>
                      </td>
                      <td>{inv.vendor_name || '—'}</td>
                      <td className="text-right font-semibold">
                        {inv.total_amount !== null && inv.total_amount !== undefined
                          ? `$${inv.total_amount.toLocaleString(undefined, { minimumFractionDigits: 2 })}`
                          : '—'}
                      </td>
                      <td>{new Date(inv.created_at).toLocaleDateString()}</td>
                      <td>
                        <span className="stage-badge">{inv.current_stage.toUpperCase()}</span>
                      </td>
                      <td>
                        <span className={getStatusClass(inv.status || '')}>
                          {getStatusText(inv.status || '')}
                        </span>
                      </td>
                      <td className="text-center">
                        <div className="actions-cell">
                          <Link
                            to={`/processing/${inv.invoice_id}`}
                            className="action-link mr-3"
                            onClick={(e) => e.stopPropagation()}
                          >
                            Review
                          </Link>
                          {inv.status === 'error' && (
                            <button className="action-button-mini text-primary" onClick={(e) => handleRetry(e, inv.invoice_id)}>
                              Retry
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>

                    {/* Expanded detail sub-row */}
                    {expandedRow === inv.invoice_id && (
                      <tr className="expanded-detail-row">
                        <td colSpan={8} style={{ padding: 0 }}>
                          <div className="expanded-detail-content">
                            {expandedDetails.has(inv.invoice_id) ? (
                              (() => {
                                const details = expandedDetails.get(inv.invoice_id)!;
                                const extracted = details.extracted_invoice;
                                return (
                                  <div className="expanded-detail-inner">
                                    {/* Key extracted fields */}
                                    <div className="expanded-fields-grid">
                                      <div className="expanded-field">
                                        <span className="expanded-field-label">Invoice Date</span>
                                        <span className="expanded-field-value">
                                          {extracted?.invoice_date
                                            ? new Date(extracted.invoice_date).toLocaleDateString()
                                            : '—'}
                                        </span>
                                      </div>
                                      <div className="expanded-field">
                                        <span className="expanded-field-label">Due Date</span>
                                        <span className="expanded-field-value">
                                          {extracted?.due_date
                                            ? new Date(extracted.due_date).toLocaleDateString()
                                            : '—'}
                                        </span>
                                      </div>
                                      <div className="expanded-field">
                                        <span className="expanded-field-label">Extraction Method</span>
                                        <span className="expanded-field-value">
                                          {extracted?.extraction_method || '—'}
                                        </span>
                                      </div>
                                      <div className="expanded-field">
                                        <span className="expanded-field-label">Current Stage</span>
                                        <span className="expanded-field-value">
                                          {details.current_stage?.toUpperCase() || '—'}
                                        </span>
                                      </div>
                                    </div>

                                    {/* Confidence Scores */}
                                    {extracted?.confidence_scores &&
                                      Object.keys(extracted.confidence_scores).length > 0 && (
                                        <div className="expanded-confidence-section">
                                          <span className="expanded-section-title">Confidence Scores</span>
                                          <div className="expanded-confidence-badges">
                                            {Object.entries(extracted.confidence_scores).map(([field, score]) => (
                                              <span key={field} className={getConfidenceClass(score)}>
                                                {field}: {(score * 100).toFixed(0)}%
                                              </span>
                                            ))}
                                          </div>
                                        </div>
                                      )}

                                    {/* Line Items mini table */}
                                    {extracted?.line_items && extracted.line_items.length > 0 && (
                                      <div className="expanded-line-items">
                                        <span className="expanded-section-title">Line Items</span>
                                        <table className="mini-table">
                                          <thead>
                                            <tr>
                                              <th>Item</th>
                                              <th className="text-right">Qty</th>
                                              <th className="text-right">Unit Price</th>
                                              <th className="text-right">Total</th>
                                            </tr>
                                          </thead>
                                          <tbody>
                                            {extracted.line_items.map((item, idx) => (
                                              <tr key={idx}>
                                                <td>{item.item_name || '—'}</td>
                                                <td className="text-right">{item.quantity ?? '—'}</td>
                                                <td className="text-right">
                                                  {item.unit_price !== null && item.unit_price !== undefined
                                                    ? `$${item.unit_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}`
                                                    : '—'}
                                                </td>
                                                <td className="text-right">
                                                  {item.total !== null && item.total !== undefined
                                                    ? `$${item.total.toLocaleString(undefined, { minimumFractionDigits: 2 })}`
                                                    : '—'}
                                                </td>
                                              </tr>
                                            ))}
                                          </tbody>
                                        </table>
                                      </div>
                                    )}

                                    {/* Validation / Approval summary if available */}
                                    {details.validation_result && (
                                      <div className="expanded-field" style={{ marginTop: '12px' }}>
                                        <span className="expanded-field-label">Validation</span>
                                        <span className="expanded-field-value">
                                          {details.validation_result.status.toUpperCase()} — Risk Score: {details.validation_result.risk_score}
                                        </span>
                                      </div>
                                    )}
                                    {details.approval_decision && (
                                      <div className="expanded-field" style={{ marginTop: '4px' }}>
                                        <span className="expanded-field-label">Approval</span>
                                        <span className="expanded-field-value">
                                          {details.approval_decision.status.toUpperCase()} — Rule: {details.approval_decision.rule_applied}
                                        </span>
                                      </div>
                                    )}
                                  </div>
                                );
                              })()
                            ) : (
                              <div className="expanded-loading">
                                <svg className="spinner text-primary" width={20} height={20} viewBox="0 0 24 24" fill="none">
                                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                                </svg>
                                <span style={{ marginLeft: '8px' }}>Loading details…</span>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))
              ) : (
                <tr>
                  <td colSpan={8} className="text-center text-muted py-6">
                    No invoices match search filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
