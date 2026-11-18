export interface Me {
  user: string;
  sso_user: string;
  name: string;
  auth: "sso" | "session" | "token";
  timezone: string;
  api: string;
  stream: string;
  logout_url: string;
}

export interface Participant {
  id: string;
  name: string;
  self: boolean;
}

export interface RoomSummary {
  room_id: string;
  peer: string;
  participants: Participant[];
  updated: string | null;
  unread: number;
  lines: number;
  last: { id: number; speaker: string; speaker_name: string; text: string; ts: string } | null;
}

export interface Line {
  id: number;
  ts: string;
  conversation_id: string;
  speaker: string;
  speaker_name: string;
  text: string;
  intent: string;
}

export interface RoomDetail {
  room_id: string;
  peer: string;
  participants: Participant[];
  read_only: true;
  integrity_ok: boolean;
  last_read: number;
  lines: Line[];
}

export interface ChatMessage {
  id: number;
  ts: string;
  role: "owner" | "agent";
  text: string;
  pending?: boolean;
}

export interface Approval {
  id: string;
  kind: "inbound_proposal" | "skill_action";
  peer: string;
  status: string;
  created: string;
  title: string;
  summary: string;
  room_id: string | null;
}

export interface Notice {
  id: string;
  ts: string;
  kind: string;
  title: string;
  body: string;
  source: string;
  source_url: string | null;
  ref_type: string | null;
  ref_id: string | null;
  read_at: string | null;
}

export interface Change {
  seq: number;
  ts: string;
  topic: "room" | "chat" | "approval" | "notification";
  ref: string;
}
