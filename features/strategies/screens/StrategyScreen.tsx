import { useState } from 'react';

import { Card, ThemedText } from '@/components/themed';
import { ErrorBanner, Screen } from '@/features/bot/components';
import { BacktestCard } from '../components/BacktestCard';
import { MarketPicker } from '../components/MarketPicker';
import { EngineCard } from '../components/EngineCard';
import { BudgetCard } from '../components/BudgetCard';
import { JournalCard } from '../components/JournalCard';
import { STRATEGIES } from '../calculations';
import { useStrategies } from '../StrategiesContext';
import type { MarketCode, StrategyCode } from '../types';

export function StrategyScreen({ code }: { code: StrategyCode }) {
  const meta = STRATEGIES[code];
  const { error } = useStrategies();
  const [market, setMarket] = useState<MarketCode>('US');
  return (
    <Screen>
      <ThemedText type="title">{meta.tab}</ThemedText>
      <Card>
        <ThemedText type="bold">{meta.name}</ThemedText>
        <ThemedText themeColor="textSecondary">{meta.blurb}</ThemedText>
        <ThemedText type="small" themeColor="textSecondary">All thresholds are untested starting defaults.</ThemedText>
      </Card>
      <MarketPicker value={market} onChange={setMarket} />
      <ErrorBanner />
      {error && <ThemedText themeColor="negative">{error}</ThemedText>}
      <EngineCard code={code} market={market} />
      <BudgetCard code={code} market={market} />
      <BacktestCard code={code} market={market} />
      <JournalCard code={code} market={market} />
    </Screen>
  );
}
