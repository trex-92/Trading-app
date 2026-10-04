import type { RealtimeChannel } from '@supabase/supabase-js';

import { supabase } from '@/lib/supabase';
import type { BotEvent, BotStatus, Order, Position } from '@/features/bot/types';

function check<T>(res: { data: T | null; error: { message: string } | null }): T | null {
  if (res.error) throw new Error(res.error.message);
  return res.data;
}

export async function fetchStatus(): Promise<BotStatus | null> {
  return check(await supabase.from('bot_status').select('*').maybeSingle<BotStatus>());
}

export async function fetchPositions(): Promise<Position[]> {
  return check(await supabase.from('positions').select('*').order('symbol').returns<Position[]>()) ?? [];
}

export async function fetchOrders(limit = 100): Promise<Order[]> {
  return (
    check(
      await supabase.from('orders').select('*').order('created_at', { ascending: false }).limit(limit).returns<Order[]>(),
    ) ?? []
  );
}

export async function fetchEvents(limit = 100): Promise<BotEvent[]> {
  return (
    check(
      await supabase.from('bot_events').select('*').order('created_at', { ascending: false }).limit(limit).returns<BotEvent[]>(),
    ) ?? []
  );
}

/** Ask the bot worker to halt/resume; it claims the row within a few seconds. */
export async function sendCommand(command: 'halt' | 'resume'): Promise<void> {
  const { error } = await supabase.from('bot_commands').insert({ command });
  if (error) throw new Error(error.message);
}

/** Calls onChange whenever any bot table changes for this user. RLS scopes the stream. */
export function subscribeToBot(onChange: () => void): () => void {
  const channel: RealtimeChannel = supabase.channel('bot');
  for (const table of ['bot_status', 'positions', 'orders', 'bot_events']) {
    channel.on('postgres_changes', { event: '*', schema: 'public', table }, onChange);
  }
  channel.subscribe();
  return () => {
    supabase.removeChannel(channel);
  };
}
