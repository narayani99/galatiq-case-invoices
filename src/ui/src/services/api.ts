import axios from 'axios';

// FastAPI base URL. Since we're running locally, default to http://localhost:8000
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const client = axios.create({
  baseURL: API_BASE_URL,
});

export interface LineItem {
  item_name: string;
  quantity: number;
  unit_price?: number;
  total?: number;
}

export interface ExtractedInvoice {
  invoice_id: string;
  vendor_name: string;
  invoice_date: string;
  due_date: string;
  total_amount: number;
  line_items: LineItem[];
  raw_text: string;
  extraction_method: string;
  confidence_scores: Record<string, number>;
  extraction_errors: string[];
}

export interface InventoryMismatch {
  item_name: string;
  requested_qty: number;
  available_stock: number;
  status: string;
}

export interface ValidationResult {
  invoice_id: string;
  status: string; // 'pass' | 'flag' | 'reject'
  mismatches: InventoryMismatch[];
  risk_score: number;
  vendor_risk?: number;
  reasoning: string;
  validated_at: string;
}

export interface ApprovalDecision {
  invoice_id: string;
  status: string; // 'approved' | 'rejected'
  reasoning: string;
  rule_applied: string;
  override_reason?: string;
  approved_at: string;
}

export interface PaymentReceipt {
  invoice_id: string;
  transaction_id: string;
  vendor_name: string;
  amount: number;
  status: string; // 'success' | 'failure' | 'skipped'
  error?: string;
  paid_at: string;
}

export interface ProcessingStateResponse {
  invoice_id: string;
  current_stage: string; // 'ingestion' | 'validation' | 'approval' | 'payment' | 'audit'
  extracted_invoice?: ExtractedInvoice;
  validation_result?: ValidationResult;
  approval_decision?: ApprovalDecision;
  payment_receipt?: PaymentReceipt;
  error_log: string[];
  created_at: string;
  updated_at: string;
  has_errors?: boolean;
}

export interface AuditEntry {
  id: number;
  invoice_id: string;
  timestamp: string;
  agent_name: string;
  action: string;
  result: string;
  reasoning?: any;
  error_msg?: string;
  duration_ms: number;
}

export interface AuditTrailResponse {
  invoice_id: string;
  audit_entries: AuditEntry[];
}

export interface InvoiceSummary {
  invoice_id: string;
  vendor_name: string;
  total_amount: number;
  status: string;
  current_stage: string;
  created_at: string;
}

export interface InvoiceListResponse {
  total: number;
  page: number;
  limit: number;
  invoices: InvoiceSummary[];
}

export interface StatsResponse {
  total_invoices: number;
  approved: number;
  rejected: number;
  processing: number;
  approved_percent: number;
  rejected_percent: number;
  total_amount_approved: number;
  avg_processing_time_ms: number;
  by_vendor: Record<string, { count: number; approved: number; total: number }>;
  by_approval_rule: Record<string, number>;
}

export interface OverrideRequest {
  action: 'approve' | 'reject' | 'reprocess';
  reason: string;
  corrected_extracted_invoice?: Record<string, any>;
}

export interface OverrideResponse {
  invoice_id: string;
  status: string;
  action: string;
}

export const api = {
  // Upload invoice file
  uploadInvoice: async (file: File): Promise<{ invoice_id: string; status: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await client.post('/api/invoices/upload', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  },

  // Get status of a single invoice
  getInvoiceStatus: async (invoiceId: string): Promise<ProcessingStateResponse> => {
    const response = await client.get(`/api/invoices/${invoiceId}`);
    return response.data;
  },

  // Get audit trail of a single invoice
  getInvoiceAudit: async (invoiceId: string): Promise<AuditTrailResponse> => {
    const response = await client.get(`/api/invoices/${invoiceId}/audit`);
    return response.data;
  },

  // List all processed invoices with filters
  listInvoices: async (params?: {
    status?: string;
    vendor?: string;
    date_from?: string;
    date_to?: string;
    page?: number;
    limit?: number;
  }): Promise<InvoiceListResponse> => {
    const response = await client.get('/api/invoices', { params });
    return response.data;
  },

  // Get global dashboard stats
  getStats: async (params?: { date_from?: string; date_to?: string }): Promise<StatsResponse> => {
    const response = await client.get('/api/stats', { params });
    return response.data;
  },

  // Retry/Resume processing of a failed invoice
  retryInvoice: async (invoiceId: string): Promise<{ invoice_id: string; status: string; resumed_from_stage: string }> => {
    const response = await client.post(`/api/invoices/${invoiceId}/retry`);
    return response.data;
  },

  // Override or reprocess invoice
  overrideInvoice: async (invoiceId: string, payload: OverrideRequest): Promise<OverrideResponse> => {
    const response = await client.post(`/api/invoices/${invoiceId}/override`, payload);
    return response.data;
  },
};
