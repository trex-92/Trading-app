import { NativeTabs } from 'expo-router/unstable-native-tabs';

import { Colors } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

const TABS = [
  { name: 'index', label: 'Overview', sf: 'chart.line.uptrend.xyaxis', md: 'monitoring' },
  { name: 'scalp', label: 'Scalp', sf: 'bolt', md: 'bolt' },
  { name: 'trend', label: 'Trend', sf: 'arrow.up.right', md: 'trending_up' },
  { name: 'range', label: 'Range', sf: 'arrow.up.and.down', md: 'swap_vert' },
  { name: 'activity', label: 'Activity', sf: 'list.bullet.rectangle', md: 'receipt_long' },
] as const;

export default function AppTabs() {
  const colors: (typeof Colors)['light'] | (typeof Colors)['dark'] = useTheme();
  return (
    <NativeTabs
      backgroundColor={colors.background}
      indicatorColor={colors.backgroundElement}
      labelStyle={{ selected: { color: colors.text } }}>
      {TABS.map((t) => (
        <NativeTabs.Trigger key={t.name} name={t.name}>
          <NativeTabs.Trigger.Label>{t.label}</NativeTabs.Trigger.Label>
          <NativeTabs.Trigger.Icon sf={t.sf} md={t.md} />
        </NativeTabs.Trigger>
      ))}
    </NativeTabs>
  );
}
