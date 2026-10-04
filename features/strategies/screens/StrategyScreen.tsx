import { Card, ThemedText } from '@/components/themed';
import { ErrorBanner, Screen } from '@/features/bot/components';
import { BacktestCard } from '../components/BacktestCard';
import { BudgetCard } from '../components/BudgetCard';
import { JournalCard } from '../components/JournalCard';
import { STRATEGIES } from '../calculations';
import { useStrategies } from '../StrategiesContext';
import type { StrategyCode } from '../types';

export function StrategyScreen({ code }: { code: StrategyCode }) {
  const meta = STRATEGIES[code];
  const { error } = useStrategies();
  return (
    <Screen>
      <ThemedText type="title">{meta.tab}</ThemedText>
      <Card>
        <ThemedText type="bold">{meta.name}</ThemedText>
        <ThemedText themeColor="textSecondary">{meta.blurb}</ThemedText>
        <ThemedText type="small" themeColor="textSecondary">All thresholds are untested starting defaults.</ThemedText>
      </Card>
      <ErrorBanner />
      {error && <ThemedText themeColor="negative">{error}</ThemedText>}
      <BudgetCard code={code} />
      <BacktestCard code={code} />
      <JournalCard code={code} />
    </Screen>
  );
}
