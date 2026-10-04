import { StyleSheet, Text, View, type TextProps, type ViewProps } from 'react-native';

import { Spacing, type ThemeColor } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

export function ThemedText({
  type = 'default',
  themeColor = 'text',
  style,
  ...rest
}: TextProps & { type?: 'default' | 'title' | 'small' | 'bold' | 'mono'; themeColor?: ThemeColor }) {
  const theme = useTheme();
  return <Text style={[{ color: theme[themeColor] }, styles[type], style]} {...rest} />;
}

export function ThemedView({
  type = 'background',
  style,
  ...rest
}: ViewProps & { type?: 'background' | 'backgroundElement' }) {
  const theme = useTheme();
  return <View style={[{ backgroundColor: theme[type] }, style]} {...rest} />;
}

/** Rounded grouped block, the building unit of every screen. */
export function Card({ style, ...rest }: ViewProps) {
  return <ThemedView type="backgroundElement" style={[styles.card, style]} {...rest} />;
}

const styles = StyleSheet.create({
  default: { fontSize: 16, lineHeight: 22 },
  title: { fontSize: 32, lineHeight: 38, fontWeight: '700' },
  small: { fontSize: 13, lineHeight: 18 },
  bold: { fontSize: 16, lineHeight: 22, fontWeight: '700' },
  mono: { fontSize: 13, lineHeight: 18, fontFamily: 'monospace' },
  card: { borderRadius: 14, padding: Spacing.three, gap: Spacing.two },
});
