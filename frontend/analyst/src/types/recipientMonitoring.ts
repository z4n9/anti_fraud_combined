export interface RecipientMonitoringSignal {
  recipient_id: string;
  label: string;
  window_start: string;
  window_end: string;
  small_transfer_count: number;
  total_amount: number;
  unique_senders: number;
  new_senders: number;
  new_sender_share: number;
  level: "high";
  reasons: string[];
  sender_breakdown: { sender_id: string; count: number; total_amount: number; new_to_recipient: boolean }[];
  transactions: { id: string; timestamp: string; sender_id: string; recipient_id: string; amount: number }[];
  details_truncated: boolean;
}
export interface RecipientMonitoringPage {
  source: { kind: "bank" | "analysis"; analysis_id: string | null; records_scanned: number; truncated: boolean; warnings: string[]; as_of: string };
  config: { window_minutes: number; max_small_amount: number; currency: string; min_small_transfers: number; min_unique_senders: number; min_new_senders: number; min_new_sender_share: number; detail_limit: number };
  total: number;
  page: number;
  page_size: number;
  items: RecipientMonitoringSignal[];
}
