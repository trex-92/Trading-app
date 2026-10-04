import { Card, ThemedText } from '@/components/themed';
import { Row } from '@/features/bot/components';
import { MARKETS, engineSummary, money } from '../calculations';
import { useStrategies } from '../StrategiesContext';
import type { MarketCode, StrategyCode } from '../types';

export function EngineCard({ code, market }: { code: StrategyCode; market: MarketCode }) {
  const { engines, markets } = useStrategies();
  const engine = engines[market] ?? null;
  const on = markets.find((m) => m.market === market)?.enabled ?? false;
  const cur = MARKETS[market].currency;
  const sum = engineSummary(engine, new Date(), code, on);
  const s = engine?.state;
  const color = sum.level === 'ok' ? 'positive' : sum.level === 'warn' ? 'warning' : 'negative';
  const mine = s?.position && s.position.strategy === code ? s.position : null;
  return (
    <Card>
      <ThemedText type="bold">Paper engine · {market}</ThemedText>
      <ThemedText themeColor={color}>{sum.text}</ThemedText>
      {s && (
        <>
          <Row left={<ThemedText themeColor="textSecondary">Risk per trade</ThemedText>}
            right={<ThemedText>{s.risk.per_trade_pct}%{s.risk.breaker_level > 0 ? ` (cut by drawdown breaker ${s.risk.breaker_level})` : ''}</ThemedText>} />
          {s.day && (
            <Row left={<ThemedText themeColor="textSecondary">Today (all strategies)</ThemedText>}
              right={<ThemedText>{s.day.trades} trades · {money(s.day.realized, cur)} · limit -{s.risk.daily_max_loss_pct}%</ThemedText>} />
          )}
          {mine && (
            <ThemedText>Open: {mine.shares} {mine.ticker} @ {mine.entry} · stop {mine.stop} · target {mine.t1}</ThemedText>
          )}
          {s.risk.live_disabled && (
            <ThemedText type="small" themeColor="negative">Drawdown breaker 2: live trading would be disabled; only paper trading is allowed.</ThemedText>
          )}
          {!s.calendar_configured && (
            <ThemedText type="small" themeColor="warning">No event calendar: FOMC, CPI/NFP and half days are NOT being skipped.</ThemedText>
          )}
          <ThemedText type="small" themeColor="textSecondary">Orders: {s.simulated_by ?? "simulated account"}. Stops are {s.stops}. If the PC or internet is down, nothing protects an open position.</ThemedText>
        </>
      )}
    </Card>
  );
}
