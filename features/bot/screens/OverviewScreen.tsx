import { Link } from 'expo-router';
import { Alert, Pressable, StyleSheet } from 'react-native';
import * as Haptics from 'expo-haptics';

import { Card, ThemedText } from '@/components/themed';
import { useTheme } from '@/hooks/use-theme';
import { useBot } from '../BotContext';
import { isStale, localDay, orderSummary, portfolioMetrics } from '../calculations';
import { ErrorBanner, Row, Screen, money, signedColor } from '../components';

export function OverviewScreen() {
  const { status, positions, orders, setHalted, loading } = useBot();
  const theme = useTheme();
  const metrics = portfolioMetrics(positions);
  const today = orderSummary(orders, localDay(new Date().toISOString()));
  const stale = isStale(status?.updated_at, new Date());

  async function toggle() {
    if (!status) return;
    const apply = async () => {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      try {
        await setHalted(!status.halted);
      } catch (e) {
        Alert.alert('Command failed', e instanceof Error ? e.message : String(e));
      }
    };
    if (status.halted) return apply();
    Alert.alert('Halt trading?', 'The bot will stop placing new orders. Open positions are not closed.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Halt', style: 'destructive', onPress: apply },
    ]);
  }

  return (
    <Screen>
      <Row left={<ThemedText type="title">Overview</ThemedText>}
        right={<Link href="/settings"><ThemedText themeColor="accent" type="bold">Settings</ThemedText></Link>} />
      <ErrorBanner />
      {!status ? (
        <ThemedText themeColor="textSecondary">
          {loading ? 'Loading…' : 'No data yet. Start the bot worker and make sure SUPABASE_USER_ID matches this account.'}
        </ThemedText>
      ) : (
        <>
          <Card>
            <ThemedText type="small" themeColor="textSecondary">
              {status.broker} · {status.env}
              {stale ? ' · BOT OFFLINE?' : ''}
            </ThemedText>
            <ThemedText type="title">{money(status.equity)}</ThemedText>
            <Row left={<ThemedText themeColor="textSecondary">Day P&L</ThemedText>}
              right={<ThemedText type="bold" themeColor={signedColor(status.day_pnl)}>{money(status.day_pnl)}</ThemedText>} />
            <Row left={<ThemedText themeColor="textSecondary">Cash</ThemedText>} right={<ThemedText>{money(status.cash)}</ThemedText>} />
            <Row left={<ThemedText themeColor="textSecondary">Unrealized</ThemedText>}
              right={<ThemedText themeColor={signedColor(metrics.unrealized)}>
                {money(metrics.unrealized)} ({metrics.unrealizedPct.toFixed(1)}%)
              </ThemedText>} />
          </Card>
          <Card>
            <ThemedText type="bold">Today</ThemedText>
            <ThemedText themeColor="textSecondary">
              {today.total} orders · {today.filled} filled · {today.blocked} blocked/rejected
            </ThemedText>
          </Card>
          <Pressable style={[styles.button, { backgroundColor: status.halted ? theme.positive : theme.negative }]} onPress={toggle}>
            <ThemedText type="bold" style={{ color: '#fff' }}>{status.halted ? 'Resume trading' : 'Halt trading'}</ThemedText>
          </Pressable>
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  button: { borderRadius: 12, padding: 16, alignItems: 'center' },
});
