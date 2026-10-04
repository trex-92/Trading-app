import { View } from 'react-native';

import { Card, ThemedText } from '@/components/themed';
import { Row } from '@/features/bot/components';
import { useTheme } from '@/hooks/use-theme';
import { MARKETS, fmtR, journalProgress, money } from '../calculations';
import { useStrategies } from '../StrategiesContext';
import type { MarketCode, StrategyCode } from '../types';

export function JournalCard({ code, market }: { code: StrategyCode; market: MarketCode }) {
  const { journal } = useStrategies();
  const theme = useTheme();
  const mine = journal.filter((t) => t.strategy === code && (t.market ?? 'US') === market);
  const p = journalProgress(mine, 'paper');
  return (
    <Card>
      <ThemedText type="bold">Paper trading results · {market}</ThemedText>
      <Row left={<ThemedText themeColor="textSecondary">Completed trades</ThemedText>} right={<ThemedText type="bold">{p.count} / {p.required}</ThemedText>} />
      <View style={{ height: 6, borderRadius: 3, backgroundColor: theme.background, overflow: 'hidden' }}>
        <View style={{ width: `${p.pct}%`, height: 6, backgroundColor: theme.accent }} />
      </View>
      {p.count === 0 ? (
        <ThemedText type="small" themeColor="textSecondary">
          No paper trades yet. They appear here once the paper engine (LIVE_STRATEGIES=yes) closes its first trade.
        </ThemedText>
      ) : (
        <>
          <Row left={<ThemedText themeColor="textSecondary">Win rate</ThemedText>} right={<ThemedText>{p.winRate?.toFixed(0)}%</ThemedText>} />
          <Row left={<ThemedText themeColor="textSecondary">Average R</ThemedText>} right={<ThemedText>{fmtR(p.avgR)}</ThemedText>} />
          <Row left={<ThemedText themeColor="textSecondary">Net P&L</ThemedText>} right={<ThemedText themeColor={p.pnl >= 0 ? 'positive' : 'negative'}>{money(p.pnl, MARKETS[market].currency)}</ThemedText>} />
        </>
      )}
      {p.count > 0 && p.count < p.required && (
        <ThemedText type="small" themeColor="textSecondary">Keep going to {p.required} trades before judging this strategy or using real money.</ThemedText>
      )}
    </Card>
  );
}
