import { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, TextInput, View } from 'react-native';

import { Card, ThemedText } from '@/components/themed';
import { Row } from '@/features/bot/components';
import { fetchBacktestResult } from '@/lib/providers/strategies';
import { useTheme } from '@/hooks/use-theme';
import { defaultRange, equityBars, fmt, fmtR, parseTickers, validateRange } from '../calculations';
import { useStrategies } from '../StrategiesContext';
import type { BacktestResult, BacktestRun, StrategyCode } from '../types';

export function BacktestCard({ code }: { code: StrategyCode }) {
  const { runs, configs, runBacktest, removeRun } = useStrategies();
  const theme = useTheme();
  const range = useMemo(() => defaultRange(new Date()), []);
  const mine = configs.find((c) => c.strategy === code);
  const [tickers, setTickers] = useState('SPY, QQQ');
  const [start, setStart] = useState(range.start);
  const [end, setEnd] = useState(range.end);
  const [budget, setBudget] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const mineRuns = runs.filter((r) => r.strategy === code);
  const shown = mineRuns.find((r) => r.id === selected) ?? mineRuns[0];
  const t = parseTickers(tickers);
  const rangeError = validateRange(start, end, new Date());
  const budgetNum = Number((budget || (mine ? String(mine.budget) : '')).replace(/,/g, ''));
  const budgetError = !(budgetNum > 0) ? 'Set a budget above, or enter one here' : null;
  const problem = t.error ?? rangeError ?? budgetError;
  const inFlight = mineRuns.some((r) => r.status === 'pending' || r.status === 'running');

  async function run() {
    setBusy(true);
    setError(null);
    try {
      await runBacktest(code, { tickers: t.tickers, start, end, budget: budgetNum });
      setSelected(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const input = [styles.input, { backgroundColor: theme.background, color: theme.text }];
  return (
    <Card>
      <ThemedText type="bold">Backtest</ThemedText>
      <ThemedText type="small" themeColor="textSecondary">
        Replays 1-minute history through this strategy and the shared risk rules. Untested default thresholds; not a forecast.
      </ThemedText>
      <Field label="Symbols (1-3)"><TextInput style={input} value={tickers} onChangeText={setTickers} autoCapitalize="characters" /></Field>
      <View style={styles.pair}>
        <View style={{ flex: 1 }}><Field label="From"><TextInput style={input} value={start} onChangeText={setStart} autoCapitalize="none" /></Field></View>
        <View style={{ flex: 1 }}><Field label="To"><TextInput style={input} value={end} onChangeText={setEnd} autoCapitalize="none" /></Field></View>
      </View>
      <Field label={`Starting capital (default: this strategy's budget${mine ? `, ${fmt(Number(mine.budget))}` : ''})`}>
        <TextInput style={input} value={budget} onChangeText={setBudget} keyboardType="decimal-pad" placeholder={mine ? String(mine.budget) : '100000'} placeholderTextColor={theme.textSecondary} />
      </Field>
      {problem && <ThemedText type="small" themeColor="warning">{problem}</ThemedText>}
      {error && <ThemedText type="small" themeColor="negative">{error}</ThemedText>}
      <Pressable style={[styles.button, { backgroundColor: theme.accent, opacity: problem || busy || inFlight ? 0.4 : 1 }]}
        disabled={Boolean(problem) || busy || inFlight} onPress={run}>
        {busy || inFlight ? <ActivityIndicator color="#fff" /> : <ThemedText type="bold" style={{ color: '#fff' }}>Run backtest</ThemedText>}
      </Pressable>
      {inFlight && <ThemedText type="small" themeColor="textSecondary">Queued on your bot. It needs to be running with BROKER=moomoo_rest.</ThemedText>}

      {mineRuns.length > 1 && (
        <View style={styles.chips}>
          {mineRuns.slice(0, 6).map((r) => (
            <Pressable key={r.id} onPress={() => setSelected(r.id)}
              style={[styles.chip, { backgroundColor: r.id === shown?.id ? theme.accent : theme.background }]}>
              <ThemedText type="small" style={r.id === shown?.id ? { color: '#fff' } : undefined}>
                {new Date(r.created_at).toLocaleDateString()} · {r.status === 'done' ? `${r.summary?.stats.n_trades ?? 0} trades` : r.status}
              </ThemedText>
            </Pressable>
          ))}
        </View>
      )}
      {shown && <RunView run={shown} onDelete={() => removeRun(shown.id).catch((e) => setError(String(e.message ?? e)))} />}
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={{ gap: 4 }}>
      <ThemedText type="small" themeColor="textSecondary">{label}</ThemedText>
      {children}
    </View>
  );
}

function RunView({ run, onDelete }: { run: BacktestRun; onDelete: () => void }) {
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const done = run.status === 'done';

  useEffect(() => {
    setResult(null);
    if (!done) return;
    let live = true;
    setLoading(true);
    fetchBacktestResult(run.id).then((r) => live && setResult(r)).catch(() => {}).finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [run.id, done]);

  if (run.status === 'pending' || run.status === 'running') {
    return <ThemedText themeColor="textSecondary">{run.status === 'pending' ? 'Waiting for the bot to pick this up…' : 'Running…'}</ThemedText>;
  }
  if (run.status === 'error') {
    return (
      <View style={{ gap: 6 }}>
        <ThemedText themeColor="negative">Failed: {run.error}</ThemedText>
        <Pressable onPress={onDelete}><ThemedText type="small" themeColor="accent">Delete this run</ThemedText></Pressable>
      </View>
    );
  }
  const s = run.summary!.stats;
  const req = run.summary!.request;
  const ci = s.expectancy_r_95ci;
  return (
    <View style={{ gap: 8 }}>
      <ThemedText type="small" themeColor="textSecondary">
        {req.tickers.join(', ')} · {req.start} to {req.end} · start {fmt(req.budget)}
      </ThemedText>
      {s.n_trades === 0 ? (
        <ThemedText>No trades in this period. Either the setups never appeared or the data was thin; check the data coverage below.</ThemedText>
      ) : (
        <>
          <Stat label="Trades" value={String(s.n_trades)} sub={`${s.days_traded} days traded`} />
          <Stat label="Win rate" value={`${s.win_rate_pct}%`} />
          <Stat label="Average R" value={fmtR(s.avg_r)} sub={ci ? `95% range ${fmtR(ci[0])} to ${fmtR(ci[1])}` : undefined} good={(s.avg_r ?? 0) > 0} />
          <Stat label="Profit factor" value={s.profit_factor == null ? '-' : String(s.profit_factor)} />
          <Stat label="Net P&L" value={fmt(s.total_pnl ?? 0)} sub={`${s.return_pct}% · costs ${fmt(s.total_costs ?? 0)}`} good={(s.total_pnl ?? 0) > 0} />
          <Stat label="Max drawdown" value={`${s.max_drawdown_pct}%`} />
          <Stat label="Avg hold" value={`${s.avg_minutes_held} min`} />
        </>
      )}
      {loading && <ActivityIndicator />}
      {result && <ResultDetails result={result} start={req.budget} />}
      {run.summary!.notes.map((n, i) => (
        <ThemedText key={i} type="small" themeColor={n.startsWith('WARNING') ? 'warning' : 'textSecondary'}>• {n}</ThemedText>
      ))}
      <Pressable onPress={onDelete}><ThemedText type="small" themeColor="accent">Delete this run</ThemedText></Pressable>
    </View>
  );
}

function Stat({ label, value, sub, good }: { label: string; value: string; sub?: string; good?: boolean }) {
  return (
    <Row left={<View><ThemedText themeColor="textSecondary">{label}</ThemedText>{sub ? <ThemedText type="small" themeColor="textSecondary">{sub}</ThemedText> : null}</View>}
      right={<ThemedText type="bold" themeColor={good === undefined ? 'text' : good ? 'positive' : 'negative'}>{value}</ThemedText>} />
  );
}

function ResultDetails({ result, start }: { result: BacktestResult; start: number }) {
  const theme = useTheme();
  const bars = equityBars(result.equity_curve, start);
  return (
    <View style={{ gap: 8 }}>
      {bars.length > 0 && (
        <View>
          <ThemedText type="small" themeColor="textSecondary">Equity after each trade</ThemedText>
          <View style={styles.chart}>
            {bars.map((b, i) => (
              <View key={i} style={{ flex: 1, height: `${b.height}%`, backgroundColor: b.up ? theme.positive : theme.negative, borderRadius: 1 }} />
            ))}
          </View>
        </View>
      )}
      <ThemedText type="bold">Data used</ThemedText>
      {Object.entries(result.data).map(([sym, d]) => (
        <ThemedText key={sym} type="small" themeColor={d.days < 5 ? 'warning' : 'textSecondary'}>
          {sym}: {d.days} days, {d.bars.toLocaleString()} bars{d.has_premarket ? ', incl. pre-market' : ', no pre-market (premarket levels unused)'}
        </ThemedText>
      ))}
      <ThemedText type="bold">Latest trades</ThemedText>
      {result.trades.slice(-15).reverse().map((tr, i) => (
        <Row key={i}
          left={<View>
            <ThemedText type="small">{tr.date} {tr.ticker} · {tr.shares} sh @ {tr.entry.toFixed(2)}</ThemedText>
            <ThemedText type="small" themeColor="textSecondary">{tr.exits.map((e) => e.reason).join(' → ')} · {tr.minutes_held} min</ThemedText>
          </View>}
          right={<ThemedText type="bold" themeColor={tr.pnl >= 0 ? 'positive' : 'negative'}>{fmtR(tr.r)}</ThemedText>} />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  input: { borderRadius: 10, padding: 12, fontSize: 16 },
  pair: { flexDirection: 'row', gap: 8 },
  button: { borderRadius: 10, padding: 14, alignItems: 'center' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: { borderRadius: 14, paddingVertical: 6, paddingHorizontal: 10 },
  chart: { height: 80, flexDirection: 'row', alignItems: 'flex-end', gap: 1, marginTop: 4 },
});
