import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Switch, TextInput, View } from 'react-native';

import { Card, ThemedText } from '@/components/themed';
import { Row } from '@/features/bot/components';
import { useBot } from '@/features/bot/BotContext';
import { useTheme } from '@/hooks/use-theme';
import { MARKETS, accountLimit, allocation, limitsOf, limitsText, money, parseMaxTrade, validateBudget } from '../calculations';
import { useStrategies } from '../StrategiesContext';
import type { MarketCode, StrategyCode } from '../types';

export function BudgetCard({ code, market }: { code: StrategyCode; market: MarketCode }) {
  const { status } = useBot();
  const { configs, markets, saveBudget, setEnabled, saveLimits } = useStrategies();
  const cur = MARKETS[market].currency;
  const theme = useTheme();
  const mine = configs.find((c) => c.strategy === code && (c.market ?? 'US') === market);
  const limit = accountLimit(market, status ? Number(status.equity) : null, markets);
  const { allocated, remaining } = allocation(configs, limit, code, market);
  const [text, setText] = useState('');
  const [cap, setCap] = useState('');
  const limits = limitsOf(mine);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setText(mine ? String(Number(mine.budget)) : '');
    setCap(limitsOf(mine).max_trade_notional ? String(limitsOf(mine).max_trade_notional) : '');
  }, [mine?.budget, mine?.params, market]); // eslint-disable-line react-hooks/exhaustive-deps

  const check = validateBudget(text, code, configs, limit, market);
  const dirty = !mine || Number(mine.budget) !== check.value;

  const capCheck = parseMaxTrade(cap, check.value ?? Number(mine?.budget ?? 0));
  const capDirty = capCheck.value !== (limits.max_trade_notional ?? 0);

  async function saveCap() {
    if (capCheck.error) return;
    setError(null);
    try {
      await saveLimits(code, market, { max_trade_notional: capCheck.value });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function save() {
    if (check.error || check.value == null) return;
    setBusy(true);
    setError(null);
    try {
      await saveBudget(code, market, check.value);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)); // the database enforces the limit too
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <ThemedText type="bold">Budget</ThemedText>
      <Row left={<ThemedText themeColor="textSecondary">{market === 'US' ? 'Account balance (Moomoo simulated)' : `${market} paper balance (Settings)`}</ThemedText>}
        right={<ThemedText>{limit == null ? (market === 'US' ? 'unknown (start the bot)' : 'not set') : money(limit, cur)}</ThemedText>} />
      <Row left={<ThemedText themeColor="textSecondary">Used by other strategies</ThemedText>}
        right={<ThemedText>{money(allocated, cur)}</ThemedText>} />
      <Row left={<ThemedText themeColor="textSecondary">Available to this one</ThemedText>}
        right={<ThemedText>{remaining == null ? '-' : money(remaining, cur)}</ThemedText>} />
      <View style={styles.inputRow}>
        <TextInput
          style={[styles.input, { backgroundColor: theme.background, color: theme.text }]}
          value={text} onChangeText={setText} keyboardType="decimal-pad" placeholder={`Budget in ${cur}`}
          placeholderTextColor={theme.textSecondary} />
        <Pressable
          style={[styles.button, { backgroundColor: theme.accent, opacity: check.error || !dirty || busy ? 0.4 : 1 }]}
          disabled={Boolean(check.error) || !dirty || busy} onPress={save}>
          {busy ? <ActivityIndicator color="#fff" /> : <ThemedText type="bold" style={{ color: '#fff' }}>Save</ThemedText>}
        </Pressable>
      </View>
      {text !== '' && check.error && <ThemedText type="small" themeColor="negative">{check.error}</ThemedText>}
      {error && <ThemedText type="small" themeColor="negative">{error}</ThemedText>}
      <ThemedText type="small" themeColor="textSecondary">
        Position size comes from this budget: risk is 1% of it per trade (0.5% after a 6% drawdown), and a trade never exceeds it.
        {market !== 'US' ? ' Shares are bought in whole lots of 100, so a small budget may not afford one lot.' : ''}
      </ThemedText>
      <ThemedText type="small" themeColor="textSecondary">Max per trade, in {cur} (optional): caps the money in any single position.</ThemedText>
      <View style={styles.inputRow}>
        <TextInput
          style={[styles.input, { backgroundColor: theme.background, color: theme.text }]}
          value={cap} onChangeText={setCap} keyboardType="decimal-pad" placeholder="No cap, e.g. 100"
          placeholderTextColor={theme.textSecondary} />
        <Pressable
          style={[styles.button, { backgroundColor: theme.accent, opacity: capCheck.error || !capDirty ? 0.4 : 1 }]}
          disabled={Boolean(capCheck.error) || !capDirty} onPress={saveCap}>
          <ThemedText type="bold" style={{ color: '#fff' }}>Save</ThemedText>
        </Pressable>
      </View>
      {cap !== '' && capCheck.error && <ThemedText type="small" themeColor="negative">{capCheck.error}</ThemedText>}
      {market === 'US' && (
        <Row left={<View>
            <ThemedText>Allow fractional shares</ThemedText>
            <ThemedText type="small" themeColor="textSecondary">
              Lets small budgets trade a fraction of a share (steps of 0.01). Moomoo's simulator may refuse fractions: run python -m bot.smoke_moomoo --fractional first.
            </ThemedText>
          </View>}
          right={<Switch value={Boolean(limits.allow_fractional)}
            onValueChange={(v) => saveLimits(code, market, { allow_fractional: v }).catch((e) => setError(String(e.message ?? e)))} />} />
      )}
      {limitsText(limits, cur) !== '' && <ThemedText type="small" themeColor="textSecondary">Applied to backtests and paper trading: {limitsText(limits, cur)}.</ThemedText>}
      <Row left={<View>
          <ThemedText>Enabled</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">The paper engine reads this within 30 seconds.</ThemedText>
        </View>}
        right={<Switch value={mine?.enabled ?? false} onValueChange={(v) => setEnabled(code, market, v).catch((e) => setError(String(e.message ?? e)))} />} />
    </Card>
  );
}

const styles = StyleSheet.create({
  inputRow: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  input: { flex: 1, borderRadius: 10, padding: 12, fontSize: 16 },
  button: { borderRadius: 10, paddingVertical: 12, paddingHorizontal: 20, alignItems: 'center', minWidth: 80 },
});
