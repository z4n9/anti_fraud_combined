export type BankEventStatus = "pending_approval" | "bank_review" | "completed" | "rejected" | "cancelled" | "expired" | "blocked_no_trusted";
export interface BankEvent {
  id: number; status: BankEventStatus; sender_name: string; recipient_name: string;
  recipient: string; amount: number; message: string; created_at: string; expires_at: string;
  participants: { invitation_id: number; user_id: number; name: string; response: "pending" | "approve" | "reject"; responded_at: string | null }[];
  anti_scam?: { pressure: boolean | null; secrecy: boolean | null; stranger: boolean | null };
  risk?: { overall_level: string; transaction_risk?: { factors: { label: string; description: string }[] }; recipient_risk?: { factors: { label: string; description: string }[] } };
  bank_decisions?: { action: string; note: string; actor_name: string; created_at: string }[];
}
export interface BankEventPage { items: BankEvent[]; total: number; page: number; page_size: number }
