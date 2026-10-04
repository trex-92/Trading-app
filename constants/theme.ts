import { Platform } from 'react-native';

export const Colors = {
  light: {
    text: '#000000',
    background: '#ffffff',
    backgroundElement: '#F0F0F3',
    backgroundSelected: '#E0E1E6',
    textSecondary: '#60646C',
    positive: '#1a7f37',
    negative: '#cf222e',
    warning: '#b25e09',
    accent: '#3c87f7',
  },
  dark: {
    text: '#ffffff',
    background: '#000000',
    backgroundElement: '#212225',
    backgroundSelected: '#2E3135',
    textSecondary: '#B0B4BA',
    positive: '#3fb950',
    negative: '#f85149',
    warning: '#e3a008',
    accent: '#5b9dff',
  },
} as const;

export type ThemeColor = keyof typeof Colors.light & keyof typeof Colors.dark;

export const Fonts = Platform.select({
  ios: { sans: 'system-ui', mono: 'ui-monospace' },
  default: { sans: 'normal', mono: 'monospace' },
});

export const Spacing = { one: 4, two: 8, three: 16, four: 24, five: 32 } as const;
export const MaxContentWidth = 800;

/** On web the tab bar floats over the top of the page; native tabs sit at the bottom. */
export const WebTopInset = Platform.OS === 'web' ? 72 : 0;
