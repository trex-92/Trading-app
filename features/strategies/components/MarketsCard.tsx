import { useState } from 'react';
import { Pressable, StyleSheet, Switch, TextInput, View } from 'react-native';

import { Card, ThemedText } from '@/components/themed';
import { Row } from '@/features/bot/components';
import { useTheme } from '@/hooks/use-theme';
import { MARKETS, MARKET_CODES, money, parseSymbols } from '../calculations';
import { useStrategies } from '../StrategiesContext';
import type { MarketCode } from '../types';

/** Settings: which markets the paper engine runs, with what symbols (and the simulated cash for SG/MY). */
export function MarketsCard() {
  return (
    <Card>
      <ThemedText type="bold">Markets to trade</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        Switch on the markets you want the paper engine to run. Switching one off stops new entries; an open position is still managed to its exit.
      </ThemedText>
      {MARKET_CODES.map((m) => <MarketRow key={m} market={m} />)}
    </Card>
  );
}

function MarketRow({ market }: { market: MarketCode }) {
  const { markets, saveMarketConfig } = useStrategies();
  const theme = useTheme();
  const meta = MARKETS[market];
  const cfg = markets.find((x) => x.market === market);
  const [symbols, setSymbols] = useState(cfg?.symbols?.length ? cfg.symbols.join(', ') : meta.sample);
  const [balance, setBalance] = useState(cfg && Number(cfg.paper_balance) > 0 ? String(Number(cfg.paper_balance)) : '');
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const local = market !== 'US';

  const parsed = parseSymbols(symbols, market);
  const bal = Number(balance.replace(/,/g, ''));
  const balanceError = local && balance !== '' && !(bal > 0) ? 'Enter an amount above zero' : null;

  async function run(fields: Parameters<typeof saveMarketConfig>[1]) {
    setError(null);
    setSaved(false);
    try {
      await saveMarketConfig(market, fields);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const input = [styles.input, { backgroundColor: theme.background, color: theme.text }];
  return (
    <View style={[styles.block, { borderTopColor: theme.backgroundSelected }]}>
      <Row left={<View>
          <ThemedText type="bold">{meta.name}</ThemedText>
          <ThemedText type="small" themeColor="textSecondary">{meta.currency} · lots of {meta.lot} · {meta.hours}</ThemedText>
        </View>}
        right={<Switch value={cfg?.enabled ?? false}
          onValueChange={(v) => {
            if (v && (parsed.error || (local && !(bal > 0)))) {
              setError(parsed.error ?? `Set a ${meta.currency} paper balance first`);
              return;
            }
            run({ enabled: v, symbols: parsed.symbols, ...(local ? { paper_balance: bal } : {}) });
          }} />} />
      <ThemedText type="small" themeColor="textSecondary">Orders: {meta.simulator}.</ThemedText>
      <TextInput style={input} value={symbols} onChangeText={(t) => { setSymbols(t); setSaved(false); }} autoCapitalize="characters" placeholder={`Symbols, e.g. ${meta.sample}`} placeholderTextColor={theme.textSecondary} />
      {local && (
        <TextInput style={input} value={balance} onChangeText={(t) => { setBalance(t); setSaved(false); }} keyboardType="decimal-pad"
          placeholder={`Paper balance in ${meta.currency}`} placeholderTextColor={theme.textSecondary} />
      )}
      {parsed.error && <ThemedText type="small" themeColor="warning">{parsed.error}</ThemedText>}
      {balanceError && <ThemedText type="small" themeColor="warning">{balanceError}</ThemedText>}
      {error && <ThemedText type="small" themeColor="negative">{error}</ThemedText>}
      <Pressable disabled={Boolean(parsed.error || balanceError)} onPress={() => run({ symbols: parsed.symbols, ...(local && bal > 0 ? { paper_balance: bal } : {}) })}
        style={[styles.save, { backgroundColor: theme.accent, opacity: parsed.error || balanceError ? 0.4 : 1 }]}>
        <ThemedText type="bold" style={{ color: '#fff' }}>{saved ? 'Saved' : 'Save symbols' + (local ? ' and balance' : '')}</ThemedText>
      </Pressable>
      {local && <ThemedText type="small" themeColor="textSecondary">The simulated cash is fixed the first time the market runs. To restart it, delete data/paper_{market}.json on the bot PC. Symbol changes apply from the next trading day.</ThemedText>}
      {cfg?.enabled && local && cfg.paper_balance > 0 && <ThemedText type="small" themeColor="textSecondary">Budget limit: {money(Number(cfg.paper_balance), meta.currency)}</ThemedText>}
    </View>
  );
}

const styles = StyleSheet.create({
  block: { gap: 8, borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 12, marginTop: 4 },
  input: { borderRadius: 10, padding: 12, fontSize: 16 },
  save: { borderRadius: 10, padding: 12, alignItems: 'center' },
});
