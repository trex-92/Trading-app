import { Pressable, StyleSheet, View } from 'react-native';

import { ThemedText } from '@/components/themed';
import { useTheme } from '@/hooks/use-theme';
import { MARKETS, MARKET_CODES } from '../calculations';
import { useStrategies } from '../StrategiesContext';
import type { MarketCode } from '../types';

/** Segmented US / SG / MY switch. A small dot marks markets that are switched on for the paper engine. */
export function MarketPicker({ value, onChange }: { value: MarketCode; onChange: (m: MarketCode) => void }) {
  const theme = useTheme();
  const { markets } = useStrategies();
  return (
    <View style={styles.row}>
      {MARKET_CODES.map((m) => {
        const on = markets.find((x) => x.market === m)?.enabled;
        return (
          <Pressable key={m} onPress={() => onChange(m)} style={[styles.seg, { backgroundColor: m === value ? theme.accent : theme.backgroundElement }]}>
            <ThemedText type="bold" style={m === value ? { color: '#fff' } : undefined}>
              {MARKETS[m].short}{on ? ' ●' : ''}
            </ThemedText>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', gap: 8 },
  seg: { flex: 1, alignItems: 'center', paddingVertical: 10, borderRadius: 10 },
});
