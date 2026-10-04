import { Card, ThemedText } from '@/components/themed';
import { Row } from '@/features/bot/components';
import { engineSummary, fmt } from '../calculations';
import { useStrategies } from '../StrategiesContext';
import type { StrategyCode } from '../types';

export function EngineCard({ code }: { code: StrategyCode }) {
  const { engine } = useStrategies();
  const sum = engineSummary(engine, new Date(), code);
  const s = engine?.state;
  const color = sum.level === 'ok' ? 'positive' : sum.level === 'warn' ? 'warning' : 'negative';
  const mine = s?.position && s.position.strategy === code ? s.position : null;
  return (
    <Card>
      <ThemedText type="bold">Paper engine</ThemedText>
      <ThemedText themeColor={color}>{sum.text}</ThemedText>
      {s && (
        <>
          <Row left={<ThemedText themeColor="textSecondary">Risk per trade</ThemedText>}
            right={<ThemedText>{s.risk.per_trade_pct}%{s.risk.breaker_level > 0 ? ` (cut by drawdown breaker ${s.risk.breaker_level})` : ''}</ThemedText>} />
          {s.day && (
            <Row left={<ThemedText themeColor="textSecondary">Today (all strategies)</ThemedText>}
              right={<ThemedText>{s.day.trades} trades · {fmt(s.day.realized)} · limit -{s.risk.daily_max_loss_pct}%</ThemedText>} />
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
          <ThemedText type="small" themeColor="textSecondary">Stops are {s.stops}. If the PC or internet is down, nothing protects an open position.</ThemedText>
        </>
      )}
    </Card>
  );
}
