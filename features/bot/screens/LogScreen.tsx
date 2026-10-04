import { Card, ThemedText } from '@/components/themed';
import { useBot } from '../BotContext';
import { ErrorBanner, Screen } from '../components';

export function LogScreen() {
  const { events } = useBot();
  return (
    <Screen>
      <ThemedText type="title">Log</ThemedText>
      <ErrorBanner />
      {events.length === 0 && <ThemedText themeColor="textSecondary">No events yet.</ThemedText>}
      {events.map((e) => (
        <Card key={e.id}>
          <ThemedText type="mono" themeColor={e.level === 'ERROR' ? 'negative' : e.level === 'WARN' ? 'warning' : 'text'}>
            {e.msg}
          </ThemedText>
          <ThemedText type="small" themeColor="textSecondary">{new Date(e.created_at).toLocaleString()}</ThemedText>
        </Card>
      ))}
    </Screen>
  );
}
