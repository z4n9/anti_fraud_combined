import type { QualityIssueLevel } from "../utils/dataQuality";

export interface SessionNotification {
  id: string;
  level: QualityIssueLevel;
  title: string;
  description: string;
  technicalCode?: string;
  items?: string[];
}
