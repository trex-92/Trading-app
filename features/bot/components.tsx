import { createContext, useContext } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View, type ScrollViewProps } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed';
import { MaxContentWidth, Spacing, WebTopInset, type ThemeColor } from '@/constants/theme';
import { useBot } from './BotContext';

export const money = (n: number) =>
  n.toLocaleString(undefined, { style: 'currency', currency: 'USD' });

export const signedColor = (n: number): ThemeColor => (n >= 0 ? 'positive' : 'negative');

/** Lets a parent (Activity) draw its own header so child screens skip the safe-area padding. */
export const ScreenInset = createContext(true);

/** Standard screen: safe-area padding, pull-to-refresh, centered max width. */
export function Screen({ children, ...rest }: ScrollViewProps) {
  const { refresh } = useBot();
  const insets = useSafeAreaInsets();
  const withInset = useContext(ScreenInset);
  return (
    <ScrollView
      contentContainerStyle={[styles.content, { paddingTop: (withInset ? insets.top + WebTopInset : 0) + Spacing.three }]}
      refreshControl={<RefreshControl refreshing={false} onRefresh={refresh} />}
      {...rest}>
      {children}
    </ScrollView>
  );
}

export function Row({ left, right }: { left: React.ReactNode; right: React.ReactNode }) {
  return (
    <View style={styles.row}>
      <View style={{ flexShrink: 1 }}>{left}</View>
      {right}
    </View>
  );
}

export function ErrorBanner() {
  const { error } = useBot();
  return error ? <ThemedText themeColor="negative">{error}</ThemedText> : null;
}

const styles = StyleSheet.create({
  content: { padding: Spacing.three, gap: Spacing.three, maxWidth: MaxContentWidth, width: '100%', alignSelf: 'center' },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: Spacing.two },
});
