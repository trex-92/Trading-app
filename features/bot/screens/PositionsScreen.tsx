import { Card, ThemedText } from '@/components/themed';
import { useBot } from '../BotContext';
import { num, positionPnl, positionPnlPct } from '../calculations';
import { ErrorBanner, Row, Screen, money, signedColor } from '../components';

export function PositionsScreen() {
  const { positions } = useBot();
  return (
    <Screen>
      <ThemedText type="title">Positions</ThemedText>
      <ErrorBanner />
      {positions.length === 0 && <ThemedText themeColor="textSecondary">No open positions.</ThemedText>}
      {positions.map((p) => (
        <Card key={p.symbol}>
          <Row
            left={<ThemedText type="bold">{p.symbol}</ThemedText>}
            right={<ThemedText type="bold" themeColor={signedColor(positionPnl(p))}>{money(positionPnl(p))}</ThemedText>} />
          <Row
            left={<ThemedText type="small" themeColor="textSecondary">{num(p.qty)} @ {num(p.avg_price).toFixed(2)} → {num(p.last_price).toFixed(2)}</ThemedText>}
            right={<ThemedText type="small" themeColor={signedColor(positionPnl(p))}>{positionPnlPct(p).toFixed(2)}%</ThemedText>} />
        </Card>
      ))}
    </Screen>
  );
}
