import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed';
import { Spacing, WebTopInset } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { ScreenInset } from '../components';
import { LogScreen } from './LogScreen';
import { OrdersScreen } from './OrdersScreen';
import { PositionsScreen } from './PositionsScreen';

const VIEWS = { Positions: PositionsScreen, Orders: OrdersScreen, Log: LogScreen } as const;

export function ActivityScreen() {
  const [view, setView] = useState<keyof typeof VIEWS>('Positions');
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const Current = VIEWS[view];
  return (
    <View style={{ flex: 1, backgroundColor: theme.background }}>
      <View style={[styles.bar, { paddingTop: insets.top + WebTopInset + Spacing.two, backgroundColor: theme.backgroundElement }]}>
        {(Object.keys(VIEWS) as (keyof typeof VIEWS)[]).map((k) => (
          <Pressable key={k} onPress={() => setView(k)} style={[styles.seg, { backgroundColor: k === view ? theme.accent : 'transparent' }]}>
            <ThemedText type="bold" style={k === view ? { color: '#fff' } : undefined}>{k}</ThemedText>
          </Pressable>
        ))}
      </View>
      <ScreenInset.Provider value={false}>
        <Current />
      </ScreenInset.Provider>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: { flexDirection: 'row', gap: Spacing.two, paddingHorizontal: Spacing.three, paddingBottom: Spacing.two },
  seg: { flex: 1, alignItems: 'center', paddingVertical: Spacing.two, borderRadius: 10 },
});
