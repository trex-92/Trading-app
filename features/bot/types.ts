export type BotStatus = {
  user_id: string;
  equity: number;
  cash: number;
  day_pnl: number;
  halted: boolean;
  broker: string;
  env: string;
  updated_at: string;
};

export type Position = {
  symbol: string;
  qty: number;
  avg_price: number;
  last_price: number;
};

export type OrderStatus = 'NEW' | 'SUBMITTED' | 'FILLED' | 'BLOCKED' | 'REJECTED';

export type Order = {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  qty: number;
  price: number | null;
  status: OrderStatus;
  reason: string;
  created_at: string;
};

export type BotEvent = {
  id: number;
  level: 'INFO' | 'WARN' | 'ERROR';
  msg: string;
  created_at: string;
};
