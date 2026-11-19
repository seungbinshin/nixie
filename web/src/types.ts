import type { CharacterId } from "./characters";
import type { Theme } from "./theme";

export interface Profile {
  character: CharacterId;
  agent_name: string;
  theme: Theme;
  chosen: boolean;
}

export interface Me {
  user: string;
  sso_user: string;
  name: string;
  auth: "sso" | "session" | "token";
  timezone: string;
  api: string;
  stream: string;
  logout_url: string;
  profile: Profile;
}

export interface Participant {
  id: string;
  name: string; // owner name
  self: boolean;
  agent_name: string | null;
  character: string | null;
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
  data?: { reply_to?: number } | null;
  temp?: boolean; // shown before the server stores it
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
  topic: "room" | "chat" | "approval" | "notification" | "archive";
  ref: string;
}

export interface WikiItem {
  id: string;
  ts: string;
  kind: string;
  title: string;
  topic: string;
  tags: string[];
  follows: string | null;
  snippet: string;
}

export interface WikiList {
  items: WikiItem[];
  topics: { topic: string; count: number; last: string }[];
  integrity_ok: boolean;
}

export interface WikiDoc {
  id: string;
  ts: string;
  kind: string;
  title: string;
  topic: string;
  tags: string[];
  body: string;
  sha256: string;
  refs: { id: string; title: string }[];
  follows: { id: string; title: string } | null;
  backlinks: { id: string; title: string; kind: string }[];
  read_only: true;
}
