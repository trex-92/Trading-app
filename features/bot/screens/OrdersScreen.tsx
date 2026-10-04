import { Card, ThemedText } from '@/components/themed';
import { useBot } from '../BotContext';
import { ErrorBanner, Row, Screen } from '../components';

export function OrdersScreen() {
  const { orders } = useBot();
  return (
    <Screen>
      <ThemedText type="title">Orders</ThemedText>
      <ErrorBanner />
      {orders.length === 0 && <ThemedText themeColor="textSecondary">No orders yet.</ThemedText>}
      {orders.map((o) => (
        <Card key={o.id}>
          <Row
            left={<ThemedText type="bold">{o.side} {o.qty} {o.symbol}</ThemedText>}
            right={<ThemedText type="small" themeColor={o.status === 'BLOCKED' || o.status === 'REJECTED' ? 'warning' : 'textSecondary'}>{o.status}</ThemedText>} />
          {o.price != null && <ThemedText type="small">@ {Number(o.price).toFixed(2)}</ThemedText>}
          {o.reason ? <ThemedText type="small" themeColor="textSecondary">{o.reason}</ThemedText> : null}
          <ThemedText type="small" themeColor="textSecondary">{new Date(o.created_at).toLocaleString()}</ThemedText>
        </Card>
      ))}
    </Screen>
  );
}
