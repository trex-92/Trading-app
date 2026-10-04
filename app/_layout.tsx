import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from 'expo-router';
import { ActivityIndicator, useColorScheme } from 'react-native';

import { ThemedText, ThemedView } from '@/components/themed';
import { AuthProvider, useAuth } from '@/features/auth/AuthContext';
import { BiometricGate } from '@/features/auth/BiometricGate';
import { BotProvider } from '@/features/bot/BotContext';
import { supabaseConfigured } from '@/lib/supabase';

function AuthGate() {
  const { session, loading } = useAuth();

  if (loading) return <ThemedView style={{ flex: 1, justifyContent: 'center' }}><ActivityIndicator /></ThemedView>;

  // Protected groups are never rendered for the wrong auth state, so tab screens can't mount without a session.
  const stack = (
    <Stack screenOptions={{ headerShown: false }}>
      <Stack.Protected guard={!session}>
        <Stack.Screen name="(auth)" />
      </Stack.Protected>
      <Stack.Protected guard={!!session}>
        <Stack.Screen name="(tabs)" />
      </Stack.Protected>
    </Stack>
  );
  // Providers mount only after sign-in so unauthenticated users never open realtime channels.
  return session ? <BiometricGate><BotProvider>{stack}</BotProvider></BiometricGate> : stack;
}

export default function RootLayout() {
  const scheme = useColorScheme();
  if (!supabaseConfigured) {
    return (
      <ThemedView style={{ flex: 1, justifyContent: 'center', padding: 24 }}>
        <ThemedText>Set EXPO_PUBLIC_SUPABASE_URL and EXPO_PUBLIC_SUPABASE_ANON_KEY (see .env.example).</ThemedText>
      </ThemedView>
    );
  }
  return (
    <ThemeProvider value={scheme === 'dark' ? DarkTheme : DefaultTheme}>
      <AuthProvider>
        <AuthGate />
      </AuthProvider>
    </ThemeProvider>
  );
}
