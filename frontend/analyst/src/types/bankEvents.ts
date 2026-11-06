export type BankEventStatus = "pending_approval" | "bank_review" | "completed" | "rejected" | "cancelled" | "expired" | "blocked_no_trusted";
export type BankMLDecision = "ALLOW" | "REVIEW" | "HIGH_RISK";
export interface BankMLAssessment {
  status: "available" | "unavailable" | "disabled";
  model_id?: string | null;
  model_version?: string | null;
  score: number | null;
  ml_decision?: BankMLDecision | null;
  rule_decision?: BankMLDecision | null;
  decision?: BankMLDecision | null;
  out_of_distribution: boolean;
  ood_reasons?: string[];
  currency?: "KZT";
  model_currency?: "KZT";
  unit_version?: string;
  amount_kzt?: number;
  monetary_thresholds_kzt?: {
    cold_start_review: number;
    cold_start_high_risk: number;
    velocity_amount_sum_1h_review: number;
    velocity_amount_sum_1h_high_risk: number;
    high_amount_indicator: number;
  };
  demo_kzt_per_eur?: number;
  thresholds: { review: number; high_risk: number };
}
export interface BankEvent {
  id: number; status: BankEventStatus; sender_name: string; recipient_name: string;
  recipient: string; amount: number; message: string; created_at: string; expires_at: string;
  participants: { invitation_id: number; user_id: number; name: string; response: "pending" | "approve" | "reject"; responded_at: string | null }[];
  anti_scam?: { pressure: boolean | null; secrecy: boolean | null; stranger: boolean | null };
  bank_review_reason?: string | null;
  resolution_note?: string | null;
  ml_assessment?: BankMLAssessment | null;
  risk?: { overall_level: string; transaction_risk?: { factors: { label: string; description: string }[] }; recipient_risk?: { factors: { label: string; description: string }[] } };
  bank_decisions?: { action: string; note: string; actor_name: string; created_at: string }[];
}
export interface BankEventPage { items: BankEvent[]; total: number; page: number; page_size: number }
